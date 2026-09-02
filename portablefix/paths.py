import sys
import tempfile
from pathlib import Path


def get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent.parent
    return Path(__file__).resolve().parent.parent


def resolve_writable_base_dir(base_dir: Path) -> tuple[Path, bool]:
    probe = base_dir / ".write_test"
    try:
        base_dir.mkdir(parents=True, exist_ok=True)
        probe.write_text("x", encoding="utf-8")
        probe.unlink(missing_ok=True)
        return base_dir, False
    except OSError:
        fallback = Path(tempfile.gettempdir()) / "PortableFix"
        try:
            fallback.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise RuntimeError(
                f"Neither {base_dir} nor the %TEMP% fallback ({fallback}) is writable. "
                "Check the TEMP environment variable."
            ) from exc
        return fallback, True
