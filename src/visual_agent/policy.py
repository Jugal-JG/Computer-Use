import re
from urllib.parse import urlsplit

from pydantic import Field, model_validator

from .contracts import Strict


class PolicyDenied(Exception):
    pass


class Policy(Strict):
    origin: str = "http://127.0.0.1:8765"
    routes: list[str] = Field(
        default_factory=lambda: [
            "/",
            "/screen",
            "/search",
            "/account",
            "/request",
            "/review",
            "/resolve",
        ]
    )
    actions: list[str] = Field(default_factory=lambda: ["click", "type_text", "key", "scroll"])
    # Optional deployment restriction. None permits visually discovered labels
    # within the configured origin/routes/actions; an empty list permits none.
    allowed_targets: list[str] | None = None
    blocked_target_pattern: str = r"\b(submit|delete|transfer|pay|purchase|send|approve|confirm)\b"
    human_targets: list[str] = Field(
        default_factory=lambda: ["Restore training session", "Acknowledge and continue"]
    )

    @model_validator(mode="after")
    def local_demo_only(self):
        parsed = urlsplit(self.origin)
        if (
            parsed.scheme != "http"
            or parsed.hostname not in {"127.0.0.1", "localhost"}
            or parsed.username
            or parsed.password
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("This demo only supports an explicit loopback HTTP origin")
        if parsed.port is None:
            raise ValueError("An explicit local port is required")
        return self

    def allows_url(self, url):
        try:
            p = urlsplit(url)
            base = urlsplit(self.origin)
            return (
                p.scheme == base.scheme
                and p.hostname == base.hostname
                and p.port == base.port
                and p.path in self.routes
                and not p.username
                and not p.password
            )
        except ValueError:
            return False

    def check(self, action, target=None, key=None, human=False):
        if action not in self.actions:
            raise PolicyDenied("ACTION_NOT_ALLOWED")
        if target is not None:
            if re.search(self.blocked_target_pattern, target, re.IGNORECASE):
                raise PolicyDenied("TARGET_NOT_ALLOWED")
            if target in self.human_targets and not human:
                raise PolicyDenied("TARGET_NOT_ALLOWED")
            if self.allowed_targets is not None:
                allowed = self.allowed_targets + (self.human_targets if human else [])
                if target not in allowed:
                    raise PolicyDenied("TARGET_NOT_ALLOWED")
        if action == "key" and key not in {
            "Tab",
            "Shift+Tab",
            "Backspace",
            "Delete",
            "ArrowLeft",
            "ArrowRight",
            "ArrowUp",
            "ArrowDown",
            "Home",
            "End",
        }:
            raise PolicyDenied("KEY_NOT_ALLOWED")
