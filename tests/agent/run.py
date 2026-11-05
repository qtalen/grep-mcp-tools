"""B04 实验编排：四臂编成、断点续跑、429 退避、离线重判与报告。

设计约束（`docs/design/batches/B04-agent-harness.md`）：
- 配置只从项目根 `.env` 读取，缺 key 启动即 fail-fast；key 不进 transcript。
- transcript 是唯一真相源：每跑一份 JSON，判分/报告是其纯函数。
- 臂 a 完整代理、臂 b 直挂 71 工具、臂 c 关注入、臂 d 关路标。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import tempfile
import time
from contextlib import AsyncExitStack
from dataclasses import asdict
from pathlib import Path
from typing import Any, Sequence

# 允许 `uv run python tests/agent/run.py` 直接执行（README 的用法）。
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

# Windows 控制台默认 GBK，报告含 U+2212 等符号会 UnicodeEncodeError；
# 显式切 UTF-8（reconfigure 只在文本流存在时可用）。
for _stream in (sys.stdout, sys.stderr):
    reconfigure = getattr(_stream, "reconfigure", None)
    if reconfigure is not None:
        reconfigure(encoding="utf-8")

from openai import APIConnectionError, APIStatusError, OpenAI, RateLimitError

from tests.agent.arms import DirectArm, ProxyArm
from tests.agent.config import AgentConfig, ConfigError, load_config
from tests.agent.fleet import (
    build_server_config,
    build_upstream_specs,
    pick_fleet_ports,
    spawn_http_demos,
)
from tests.agent.judge import judge
from tests.agent.loop import ChatResponse, Usage, run_tool_loop
from tests.agent.report import build_report, render_markdown
from tests.agent.tasks import TASKS, TaskSpec
from tests.agent.transcript import (
    Transcript,
    assert_no_secret,
    load_transcript,
    make_run_id,
    save_transcript,
)
from tests.agent.variants import AblatedArm
from tests.e2e.harness import write_config

DEFAULT_ARMS: tuple[str, ...] = ("a", "b", "c", "d")
DEFAULT_TRANSCRIPTS_DIR = Path("tests/agent/transcripts")
MAX_TURNS = 16
CHAT_RETRY_ATTEMPTS = 5
PING_MESSAGES: list[dict] = [{"role": "user", "content": "ping"}]


# ---------- 纯逻辑 ----------


def backoff_schedule(attempts: int, base: float = 1.0, cap: float = 30.0) -> list[float]:
    """指数退避序列：base, 2*base, 4*base... 逐项封顶。"""
    return [min(float(base) * (2**index), float(cap)) for index in range(max(0, attempts))]


def is_run_complete(path: Path) -> bool:
    """断点续跑判定：文件存在、可载入且已有判分结论。"""
    path = Path(path)
    if not path.is_file():
        return False
    try:
        transcript = load_transcript(path)
    except Exception:
        return False
    return transcript.verdict is not None


def build_parser() -> argparse.ArgumentParser:
    """命令行旗标定义。"""
    parser = argparse.ArgumentParser(
        description="B04 agent harness：四臂实验、判分与报告"
    )
    parser.add_argument(
        "--arms",
        default=",".join(DEFAULT_ARMS),
        help="逗号分隔，默认 a,b,c,d",
    )
    parser.add_argument("--tasks", default="all", help="逗号分隔任务 id，默认全部")
    parser.add_argument("--reps", type=int, default=5, help="每任务重复次数，默认 5")
    parser.add_argument(
        "--judge-only", action="store_true", help="只重判已有 transcript，不跑模型"
    )
    parser.add_argument(
        "--report-only", action="store_true", help="只聚合已有 transcript 出报告"
    )
    parser.add_argument(
        "--transcripts-dir",
        default=str(DEFAULT_TRANSCRIPTS_DIR),
        help="transcript 目录，默认 tests/agent/transcripts",
    )
    return parser


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """解析并校验旗标（臂与任务 id 白名单校验）。"""
    parser = build_parser()
    args = parser.parse_args(argv)

    arms = [part.strip() for part in str(args.arms).split(",") if part.strip()]
    unknown_arms = [arm for arm in arms if arm not in DEFAULT_ARMS]
    if not arms or unknown_arms:
        parser.error(
            f"unknown arm(s): {unknown_arms or args.arms!r}; "
            f"choose from {', '.join(DEFAULT_ARMS)}"
        )
    args.arms = arms

    raw_tasks = str(args.tasks).strip()
    known_tasks = {task.id for task in TASKS}
    if raw_tasks.casefold() in ("", "all"):
        args.tasks = [task.id for task in TASKS]
    else:
        chosen = [part.strip() for part in raw_tasks.split(",") if part.strip()]
        unknown_tasks = [task for task in chosen if task not in known_tasks]
        if not chosen or unknown_tasks:
            parser.error(
                f"unknown task(s): {unknown_tasks or args.tasks!r}; "
                f"choose from {', '.join(sorted(known_tasks))}"
            )
        args.tasks = chosen

    if args.reps < 1:
        parser.error("--reps must be >= 1")

    args.transcripts_dir = Path(args.transcripts_dir)
    return args


# ---------- chat 客户端 ----------


def _message_to_dict(message: Any) -> dict:
    """把服务端 message 原样转成可 append 的 dict（保留 reasoning_content）。"""
    if isinstance(message, dict):
        data = dict(message)
    elif hasattr(message, "model_dump"):
        data = message.model_dump()
    else:
        data = dict(vars(message))
    extra = getattr(message, "model_extra", None)
    if isinstance(extra, dict):
        for key, value in extra.items():
            data.setdefault(key, value)
    return data


class OpenAIChat:
    """OpenAI 兼容 chat.completions 的最小同步封装。"""

    def __init__(self, cfg: AgentConfig) -> None:
        self.cfg = cfg
        self.client = OpenAI(base_url=cfg.base_url, api_key=cfg.api_key)

    def create(
        self, messages: list[dict], tools: list[dict] | None = None
    ) -> ChatResponse:
        kwargs: dict[str, Any] = {
            "model": self.cfg.model,
            "messages": messages,
            "reasoning_effort": self.cfg.reasoning_effort,
        }
        if tools:
            kwargs["tools"] = tools
        completion = self.client.chat.completions.create(**kwargs)
        usage = getattr(completion, "usage", None)
        return ChatResponse(
            message=_message_to_dict(completion.choices[0].message),
            usage=Usage(
                prompt_tokens=int(getattr(usage, "prompt_tokens", 0) or 0),
                completion_tokens=int(getattr(usage, "completion_tokens", 0) or 0),
                total_tokens=int(getattr(usage, "total_tokens", 0) or 0),
            ),
        )


# ---------- 常驻 token 隔离测量 ----------


def measure_resident(chat: Any, tools: list[dict]) -> dict:
    """测该臂工具清单的常驻 prompt token 成本（减去无工具基线）。"""
    without = chat.create(list(PING_MESSAGES), [])
    with_tools = chat.create(list(PING_MESSAGES), tools)
    return {
        "with_tools": int(with_tools.usage.prompt_tokens),
        "without_tools": int(without.usage.prompt_tokens),
        "tools_prompt_tokens": int(
            with_tools.usage.prompt_tokens - without.usage.prompt_tokens
        ),
        "tools_chars": len(json.dumps(tools, ensure_ascii=False)),
    }


# ---------- 单跑与重试 ----------


class ChatRetryError(RuntimeError):
    """chat 请求重试耗尽。"""


async def _run_chat_with_retry(
    chat: Any,
    arm_impl: Any,
    task: TaskSpec,
    tools: list[dict],
    attempts: int = CHAT_RETRY_ATTEMPTS,
):
    """跑一次工具循环；限流/连接/状态错误按指数退避重试（整跑重来）。"""
    delays = [0.0, *backoff_schedule(attempts)]
    last_error: Exception | None = None
    for delay in delays:
        if delay:
            await asyncio.sleep(delay)
        try:
            return await run_tool_loop(
                chat,
                task.prompt,
                tools,
                arm_impl.call_upstream,
                max_turns=MAX_TURNS,
            )
        except (RateLimitError, APIConnectionError, APIStatusError) as error:
            last_error = error
    raise ChatRetryError(
        f"chat retries exhausted: {type(last_error).__name__}"
    ) from last_error


def _record_transcript(
    arm: str,
    task: TaskSpec,
    rep: int,
    result: Any,
    duration_s: float,
    api_key: str,
    out_dir: Path,
) -> Transcript:
    """判分 + 落盘（含密钥出口断言）。"""
    tool_calls = [asdict(record) for record in result.tool_calls]
    verdict = judge(task, arm, tool_calls, result.final_text, result.gave_up)
    transcript = Transcript(
        run_id=make_run_id(arm, task.id, rep),
        arm=arm,
        task_id=task.id,
        rep=rep,
        messages=result.messages,
        tool_calls=tool_calls,
        usages=[asdict(usage) for usage in result.usages],
        final_text=result.final_text,
        gave_up=result.gave_up,
        verdict=asdict(verdict),
        duration_s=duration_s,
    )
    assert_no_secret(asdict(transcript), api_key)
    save_transcript(transcript, out_dir)
    return transcript


def _record_failure(
    arm: str,
    task: TaskSpec,
    rep: int,
    error: BaseException,
    duration_s: float,
    api_key: str,
    out_dir: Path,
) -> Transcript:
    """异常兜底：把失败的 run 也落成一条 gave_up transcript，后续可续跑。"""
    transcript = Transcript(
        run_id=make_run_id(arm, task.id, rep),
        arm=arm,
        task_id=task.id,
        rep=rep,
        messages=[],
        tool_calls=[],
        usages=[],
        final_text="",
        gave_up=True,
        verdict={
            "passed": False,
            "reason": f"run failed: {type(error).__name__}",
            "hallucinated": False,
            "strict_passed": False,
            "extra_calls": [],
        },
        duration_s=duration_s,
    )
    assert_no_secret(asdict(transcript), api_key)
    save_transcript(transcript, out_dir)
    return transcript


async def run_experiment(
    args: argparse.Namespace,
    chat: Any | None = None,
    cfg: AgentConfig | None = None,
) -> dict:
    """按臂编成上游，逐任务 × 遍跑实验；返回各臂常驻 token 测量。"""
    if cfg is None:
        cfg = load_config()
    if chat is None:
        chat = OpenAIChat(cfg)

    tasks = [task for task in TASKS if task.id in set(args.tasks)]
    out_dir = Path(args.transcripts_dir)
    ports = pick_fleet_ports()
    demos = spawn_http_demos(ports)
    resident: dict = {}
    try:
        with tempfile.TemporaryDirectory(prefix="b04-config-") as temp_dir:
            config_path = write_config(
                Path(temp_dir),
                build_server_config(ports),
                settings={"refresh_interval": 600, "handshake_timeout": 10},
            )
            async with AsyncExitStack() as stack:
                proxy = None
                if any(arm in ("a", "c", "d") for arm in args.arms):
                    proxy = await stack.enter_async_context(ProxyArm(config_path))

                impls: dict[str, Any] = {}
                for arm in args.arms:
                    if arm == "b":
                        impls[arm] = await stack.enter_async_context(
                            DirectArm(build_upstream_specs(ports))
                        )
                    elif arm == "c":
                        impls[arm] = AblatedArm(proxy, strip_description=True)
                    elif arm == "d":
                        impls[arm] = AblatedArm(proxy, strip_signpost=True)
                    else:
                        impls[arm] = proxy

                tool_lists = {
                    arm: await impl.list_openai_tools()
                    for arm, impl in impls.items()
                }
                resident = {
                    arm: measure_resident(chat, tool_lists[arm]) for arm in args.arms
                }
                _save_resident(resident, out_dir)

                for arm in args.arms:
                    impl = impls[arm]
                    tools = tool_lists[arm]
                    for task in tasks:
                        for rep in range(1, args.reps + 1):
                            path = out_dir / f"{make_run_id(arm, task.id, rep)}.json"
                            if is_run_complete(path):
                                continue
                            started = time.monotonic()
                            try:
                                result = await _run_chat_with_retry(
                                    chat, impl, task, tools
                                )
                            except Exception as error:
                                # 单 run 兜底：不中断整批；KeyboardInterrupt /
                                # SystemExit / asyncio.CancelledError 不是 Exception
                                # 子类，会正常向上传播。
                                _record_failure(
                                    arm,
                                    task,
                                    rep,
                                    error,
                                    time.monotonic() - started,
                                    cfg.api_key,
                                    out_dir,
                                )
                                continue
                            duration = time.monotonic() - started
                            _record_transcript(
                                arm,
                                task,
                                rep,
                                result,
                                duration,
                                cfg.api_key,
                                out_dir,
                            )
    finally:
        for demo in reversed(demos):
            demo.stop()
    return resident


# ---------- 离线路径 ----------


def _resident_path(directory: Path) -> Path:
    return Path(directory) / "resident.json"


def _save_resident(resident: dict, directory: Path) -> Path:
    """把各臂常驻测量落盘，供 `--report-only` 离线复算。"""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    path = _resident_path(directory)
    path.write_text(
        json.dumps(resident, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return path


def _load_resident(directory: Path) -> dict | None:
    """读 `resident.json`；不存在或损坏时返回 None。"""
    path = _resident_path(directory)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def _load_transcripts(directory: Path) -> list[Transcript]:
    directory = Path(directory)
    if not directory.is_dir():
        return []
    transcripts: list[Transcript] = []
    for path in sorted(directory.glob("*.json")):
        try:
            transcripts.append(load_transcript(path))
        except Exception:
            continue
    return transcripts


def _rejudge(directory: Path) -> None:
    tasks_by_id = {task.id: task for task in TASKS}
    directory = Path(directory)
    if not directory.is_dir():
        return
    for path in sorted(directory.glob("*.json")):
        try:
            transcript = load_transcript(path)
        except Exception:
            continue
        task = tasks_by_id.get(transcript.task_id)
        if task is None:
            continue
        verdict = judge(
            task,
            transcript.arm,
            transcript.tool_calls,
            transcript.final_text,
            transcript.gave_up,
        )
        transcript.verdict = asdict(verdict)
        save_transcript(transcript, directory)


def _print_report(transcripts: Sequence[Transcript], resident: dict | None) -> dict:
    report = build_report(transcripts, TASKS, resident=resident)
    print(render_markdown(report))
    return report


def main(argv: Sequence[str] | None = None) -> int:
    """入口：默认跑实验后出报告；支持离线重判/聚合。"""
    args = parse_args(argv)

    if args.report_only:
        _print_report(
            _load_transcripts(args.transcripts_dir),
            _load_resident(args.transcripts_dir),
        )
        return 0

    if args.judge_only:
        _rejudge(args.transcripts_dir)
        _print_report(
            _load_transcripts(args.transcripts_dir),
            _load_resident(args.transcripts_dir),
        )
        return 0

    try:
        cfg = load_config()
    except ConfigError as error:
        print(
            "配置缺失，无法运行实验。\n"
            f"{error}\n"
            "提示：复制 .env.example 为 .env，并填写 DEEPSEEK_BASE_URL 与 "
            "DEEPSEEK_API_KEY（只读取项目根 .env，不读进程环境变量）。",
            file=sys.stderr,
        )
        return 2

    resident = asyncio.run(run_experiment(args, cfg=cfg))
    _print_report(
        _load_transcripts(args.transcripts_dir),
        _load_resident(args.transcripts_dir) or resident,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
