import asyncio
import hashlib
import json
import re
import time
import uuid

from .contracts import Condition, Outputs, Result, Target
from .evidence import Evidence
from .interfaces import Surface
from .policy import PolicyDenied
from .vision import Unresolved, View, normalize


class StopRun(Exception):
    def __init__(self, code, status="failure"):
        self.code = code
        self.status = status


class Run:
    def __init__(
        self,
        surface: Surface,
        artifact,
        inputs,
        directory,
        intervention_timeout=300,
        operator_kind="human",
        step_delay_ms=0,
    ):
        self.surface = surface
        self.artifact = artifact
        self.inputs = inputs
        self.id = uuid.uuid4().hex
        self.evidence = Evidence(directory, self.id)
        self.owner = "automation"
        self.state = "RUNNING"
        self.generation = 0
        self.step = None
        self.expected = None
        self.reason = None
        self.llm_calls = 0
        self.lock = asyncio.Lock()
        self.resume_event = asyncio.Event()
        self.last_view = None
        self.last_view_digest = None
        self.last_observed_time = 0.0
        self.intervention_timeout = intervention_timeout
        self.abort = False
        self.recoveries = {}
        self.operator_url = None
        self.focus_target = None
        self.operator_kind = operator_kind
        self.step_delay_ms = step_delay_ms
        self.interventions = 0
        self.evidence.event(
            "run_started",
            session_id=surface.session_id,
            artifact_hash=artifact.digest(),
        )

    async def observe(self):
        started = time.monotonic()
        png = await self.surface.observe()
        captured = time.monotonic()
        digest = hashlib.sha256(png).digest()
        reused = self.last_view is not None and digest == self.last_view_digest
        view = self.last_view if reused else await asyncio.to_thread(View, png)
        self.last_view = view
        self.last_view_digest = digest
        self.last_observed_time = time.monotonic()
        self.evidence.event(
            "observation",
            capture_ms=round((captured - started) * 1000, 1),
            perception_ms=round((self.last_observed_time - captured) * 1000, 1),
            ocr_reused=reused,
            viewport=[view.width, view.height],
        )
        return view

    def checkpoint_ok(self, view, condition):
        if not view.has(condition.text):
            return False
        if condition.kind == "text":
            return True
        try:
            value = view.read_field(self.artifact.targets[condition.target])
            return normalize(value) == normalize(getattr(self.inputs, condition.input))
        except Unresolved:
            return False

    async def intervention(self, code, condition):
        async with self.lock:
            self.interventions += 1
            self.owner = "none"
            self.state = "WAITING_FOR_HUMAN"
            self.generation += 1
            self.reason = code
            self.resume_condition = condition
            self.resume_event.clear()
            self.evidence.event(
                "intervention_requested",
                step=self.step,
                code=code,
                session_id=self.surface.session_id,
                generation=self.generation,
            )
            self.evidence.checkpoint(self.state, self.step)
            if self.last_view:
                self.evidence.screenshot(self.last_view.png, "intervention.png", self.last_view)
        print(
            f"Intervention: {code}. Operator: {self.operator_url or 'not attached'}",
            flush=True,
        )
        try:
            await asyncio.wait_for(self.resume_event.wait(), timeout=self.intervention_timeout)
        except TimeoutError:
            raise StopRun("INTERVENTION_TIMEOUT")
        if self.abort:
            raise StopRun("OPERATOR_ABORTED")

    async def take_control(self, generation):
        async with self.lock:
            if generation != self.generation or self.state != "WAITING_FOR_HUMAN":
                raise PolicyDenied("STALE_CONTROL")
            self.owner = "human"
            self.state = "HUMAN_CONTROL"
            self.generation += 1
            self.evidence.event(
                "human_control_acquired", generation=self.generation, actor=self.operator_kind
            )

    async def human_action(self, data):
        async with self.lock:
            if self.owner != "human" or data["generation"] != self.generation:
                raise PolicyDenied("STALE_CONTROL")
            view = await self.observe()
            kind = data["kind"]
            if kind == "click":
                x = float(data["x"])
                y = float(data["y"])
                # Clicks are visibly grounded in an allowed control, even for the operator.
                candidates = []
                from .contracts import Target

                targets = list(self.artifact.targets.values()) + [
                    Target(text=t) for t in self.surface.policy.human_targets
                ]
                for target in targets:
                    try:
                        b = view.resolve(target)
                        if b.x - 8 <= x <= b.x + b.w + 8 and b.y - 8 <= y <= b.y + b.h + 8:
                            candidates.append((target, b))
                    except Unresolved:
                        pass
                if len(candidates) != 1:
                    raise PolicyDenied("HUMAN_TARGET_UNRESOLVED")
                target, b = candidates[0]
                self.surface.policy.check("click", target.text, human=True)
                await self.surface.click(*b.center)
                self.focus_target = target if target.kind == "field" else None
                self.evidence.event("human_action", kind=kind, target=target.text, x=x, y=y)
            elif kind == "type_text":
                if not self.focus_target:
                    raise PolicyDenied("NO_VERIFIED_FIELD")
                b = view.resolve(self.focus_target)
                self.surface.policy.check(kind, self.focus_target.text, human=True)
                await self.surface.click(*b.center)
                await self.surface.type_text(data.get("text", "")[:100])
                self.evidence.event(
                    "human_action",
                    kind=kind,
                    target=self.focus_target.text,
                    value="[REDACTED]",
                )
            elif kind == "key":
                self.surface.policy.check(kind, key=data.get("key"), human=True)
                await self.surface.key(data["key"])
                self.focus_target = None
                self.evidence.event("human_action", kind=kind, key=data["key"])
            elif kind == "scroll":
                self.surface.policy.check(kind, human=True)
                await self.surface.scroll(data.get("delta", 0))
                self.evidence.event("human_action", kind=kind)
            else:
                raise PolicyDenied("ACTION_NOT_ALLOWED")

    async def resume(self, generation):
        async with self.lock:
            if self.owner != "human" or generation != self.generation:
                raise PolicyDenied("STALE_CONTROL")
            self.state = "VERIFYING_RESUME"
            self.owner = "none"
            self.generation += 1
            view = await self.observe()
            exceptional = (
                view.modal or view.occluded or any(view.has(h.text) for h in self.artifact.handlers)
            )
            if exceptional or not self.checkpoint_ok(view, self.resume_condition):
                self.owner = "human"
                self.state = "HUMAN_CONTROL"
                self.evidence.event("resume_rejected", expected=self.resume_condition.text)
                raise PolicyDenied("CHECKPOINT_NOT_REACHED")
            self.owner = "automation"
            self.state = "RUNNING"
            self.reason = None
            self.evidence.event("resume_verified", session_id=self.surface.session_id)
            self.resume_event.set()

    async def handle(self, view, condition):
        if view.occluded:
            await self.intervention("OCCLUDED_OR_UNKNOWN_LAYER", condition)
            return True
        for handler in self.artifact.handlers:
            if not view.has(handler.text):
                continue
            self.evidence.event("condition_detected", code=handler.code, response=handler.response)
            if handler.response in {"business_outcome", "failure"}:
                raise StopRun(handler.code, handler.response)
            if handler.response == "intervention":
                await self.intervention(handler.code, condition)
                return True
            count = self.recoveries.get(handler.code, 0)
            if count >= handler.max_attempts:
                raise StopRun("RECOVERY_EXHAUSTED")
            self.recoveries[handler.code] = count + 1
            target = handler.locator or self.artifact.targets[handler.target]
            async with self.lock:
                if self.owner != "automation":
                    raise PolicyDenied("CONTROL_NOT_OWNED")
                b = view.resolve(target)
                self.surface.policy.check("click", target.text)
                await self.surface.click(*b.center)
            self.evidence.event(
                "recovery_action", target=handler.target or target.text, attempt=count + 1
            )
            # Dispatch is not completion. Wait for the notice to disappear without
            # sending a second click while its navigation is still in flight.
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                await asyncio.sleep(0.25)
                after = await self.observe()
                if not after.has(handler.text):
                    return True
            raise StopRun("RECOVERY_EXHAUSTED")
        if view.modal:
            await self.intervention("UNKNOWN_DIALOG", condition)
            return True
        return False

    async def wait_condition(self, condition, timeout_ms=5000, reuse_recent=False):
        # The preceding verified postcondition can satisfy the next precondition.
        # execute_step still takes a fresh screenshot before dispatch.
        if (
            reuse_recent
            and self.last_view
            and time.monotonic() - self.last_observed_time < 0.75
            and not self.last_view.modal
            and not self.last_view.occluded
            and self.checkpoint_ok(self.last_view, condition)
        ):
            return self.last_view
        deadline = time.monotonic() + timeout_ms / 1000
        searched = False
        searches = 0
        while True:
            view = await self.observe()
            if await self.handle(view, condition):
                deadline = time.monotonic() + timeout_ms / 1000
                continue
            if (
                view.has("Member Details")
                and len(view.find(self.inputs.member_id, (0.12, 0.18, 0.98, 0.94))) != 1
            ):
                raise StopRun("MEMBER_IDENTITY_MISMATCH")
            if self.checkpoint_ok(view, condition):
                return view
            if not searched:
                searched = True
                searches += 1
                try:
                    # Verify the heading first, then independently verify the
                    # field. They need not fit in a single screenshot.
                    if not view.has(condition.text):
                        view, _ = await self.scroll_to_target(
                            view, Target(text=condition.text), condition
                        )
                    if condition.kind == "text":
                        return view
                    target = self.artifact.targets[condition.target]

                    def verified_field(current):
                        value = current.read_field(target)
                        if normalize(value) != normalize(getattr(self.inputs, condition.input)):
                            raise Unresolved("TARGET_NOT_FOUND")
                        return value

                    try:
                        verified_field(view)
                    except Unresolved:
                        view, _ = await self.scroll_to_target(
                            view, target, condition, reader=verified_field
                        )
                    return view
                except Unresolved as exc:
                    if exc.code != "TARGET_NOT_FOUND":
                        await self.intervention(exc.code, condition)
                    deadline = time.monotonic() + timeout_ms / 1000
            if time.monotonic() >= deadline:
                if searches < 2:
                    searched = False
                    deadline = time.monotonic() + timeout_ms / 1000
                    continue
                await self.intervention("UNEXPECTED_STATE", condition)
                deadline = time.monotonic() + timeout_ms / 1000
            await asyncio.sleep(0.25)

    async def scroll_to_target(self, view, target, condition, reader=None):
        """Search pixels in bounded, overlapping scrolls on a verified page."""
        for direction in (1, -1):
            for attempt in range(8):
                before = {(w.text, w.x, w.y) for w in view.base_words}
                async with self.lock:
                    if self.owner != "automation":
                        raise PolicyDenied("CONTROL_NOT_OWNED")
                    self.surface.policy.check("scroll")
                    delta = direction * min(500, max(1, int(view.height * 0.4)))
                    await self.surface.scroll(delta)
                self.evidence.event(
                    "target_search_scroll", target=target.text, delta=delta, attempt=attempt + 1
                )
                await asyncio.sleep(0.2)
                after = await self.observe()
                if await self.handle(after, condition):
                    raise Unresolved("TARGET_NOT_FOUND")
                # Retain page context only while current and previous screenshots
                # share stationary-x OCR content. No heading need stay on screen.
                resized = (after.width, after.height) != (view.width, view.height)
                overlap = any(
                    a.text == b.text and (resized or abs(a.x - b.x) <= 3)
                    for a in view.base_words
                    for b in after.base_words
                )
                if not overlap:
                    raise Unresolved("TARGET_NOT_FOUND")
                try:
                    return after, reader(after) if reader else after.resolve(target)
                except Unresolved as exc:
                    if exc.code != "TARGET_NOT_FOUND":
                        raise
                unchanged = before == {(w.text, w.x, w.y) for w in after.base_words}
                view = after
                if unchanged:
                    break
        raise Unresolved("TARGET_NOT_FOUND")

    async def execute_step(self, step, *, verify_post=True):
        self.step = step.id
        self.expected = step.post.text
        self.evidence.checkpoint("RUNNING", step.id)
        if self.step_delay_ms:
            await asyncio.sleep(self.step_delay_ms / 1000)
        view = await self.wait_condition(
            Condition(text=step.pre), step.timeout_ms, reuse_recent=True
        )
        target = self.artifact.targets[step.target]
        while True:
            # Ground the action in a new screenshot. A resize or late popup makes
            # the earlier candidate stale and forces a complete re-resolution.
            view = await self.observe()
            if await self.handle(view, Condition(text=step.pre)):
                await self.wait_condition(Condition(text=step.pre), step.timeout_ms)
                continue
            if not self.checkpoint_ok(view, Condition(text=step.pre)):
                await self.wait_condition(Condition(text=step.pre), step.timeout_ms)
                continue
            try:
                try:
                    box = view.resolve(target)
                except Unresolved as exc:
                    if exc.code != "TARGET_NOT_FOUND":
                        raise
                    view, box = await self.scroll_to_target(view, target, Condition(text=step.pre))
                if getattr(self.surface, "window_following", False):
                    # A user may drag the window while OCR is running. Check its
                    # current dimensions once more before using the resolved box.
                    await self.surface.observe()
                    if self.surface.dimensions != (view.width, view.height):
                        continue
                break
            except Unresolved as exc:
                await self.intervention(exc.code, Condition(text=step.pre))
        async with self.lock:
            if self.owner != "automation":
                raise PolicyDenied("CONTROL_NOT_OWNED")
            self.surface.policy.check(step.action, target.text)
            await self.surface.click(*box.center)
        if step.action == "type_text":
            # All callers share this execution path. Search again
            # if reflow moved the focused field outside the visible viewport.
            for attempt in range(3):
                focused = await self.observe()
                if await self.handle(focused, Condition(text=step.pre)):
                    focused = await self.wait_condition(Condition(text=step.pre), step.timeout_ms)
                try:
                    current_box = focused.resolve(target)
                except Unresolved as exc:
                    if exc.code != "TARGET_NOT_FOUND":
                        await self.intervention(exc.code, Condition(text=step.pre))
                        continue
                    try:
                        focused, current_box = await self.scroll_to_target(
                            focused, target, Condition(text=step.pre)
                        )
                    except Unresolved as search_error:
                        await self.intervention(search_error.code, Condition(text=step.pre))
                        continue
                box, view = current_box, focused
                async with self.lock:
                    if self.owner != "automation":
                        raise PolicyDenied("CONTROL_NOT_OWNED")
                    self.surface.policy.check(step.action, target.text)
                    await self.surface.click(*box.center)
                    await self.surface.type_text(getattr(self.inputs, step.input))
                after = await self.observe()
                if (after.width, after.height) == (view.width, view.height):
                    break
                # A resize during insertion can invalidate focus. Retry only this
                # idempotent field fill, never arbitrary button clicks.
                try:
                    if normalize(after.read_field(target)) == normalize(
                        getattr(self.inputs, step.input)
                    ):
                        break
                except Unresolved:
                    pass
                if attempt == 2:
                    await self.intervention("UNEXPECTED_STATE", step.post)
        self.evidence.event(
            "action",
            step=step.id,
            kind=step.action,
            target=step.target,
            actor="automation",
            policy="allowed",
            x=box.center[0],
            y=box.center[1],
            viewport=[view.width, view.height],
            input_ref=step.input,
        )
        if verify_post:
            await self.wait_condition(step.post, step.timeout_ms)
            self.evidence.event("checkpoint_passed", step=step.id, condition=step.post.text)

    async def extract(self):
        view = await self.wait_condition(Condition(text=self.artifact.success_condition))
        bindings = self.artifact.extractions

        async def read_output(name, masked=False):
            nonlocal view
            label = bindings[name].label
            target = bindings[name].target or Target(
                text=label, kind="value", relationships=("right_of",)
            )

            def read(current):
                try:
                    value = current.read_value(target, masked=masked)
                    if not value:
                        raise Unresolved("TARGET_NOT_FOUND")
                    return value
                except Unresolved as exc:
                    if exc.code in {"TARGET_AMBIGUOUS", "TARGET_LOW_CONFIDENCE"}:
                        raise
                    raise Unresolved("TARGET_NOT_FOUND")

            try:
                return read(view)
            except Unresolved:
                view, value = await self.scroll_to_target(
                    view,
                    target,
                    Condition(text=self.artifact.success_condition),
                    reader=read,
                )
                return value

        account = await read_output("masked_account", masked=True)
        # OCR may merge repeated mask glyphs. Preserve the four observed digits;
        # normalize only the non-sensitive masking prefix, never the identifier.
        match = re.fullmatch(r"\*{2,6}(\d{4})", account)
        if not match:
            raise StopRun("OUTPUT_UNREADABLE")
        account = "****" + match.group(1)
        month = (await read_output("statement_month")).replace(" ", "")
        delivery = (await read_output("delivery_method")).strip().lower()
        if month != self.inputs.statement_month or delivery != self.inputs.delivery_method:
            raise StopRun("OUTPUT_MISMATCH")
        return Outputs(masked_account=account, statement_month=month, delivery_method=delivery)

    def finish(self, status, code, outputs=None):
        self.state = "TERMINAL"
        self.owner = "none"
        self.generation += 1
        result = Result(
            run_id=self.id,
            session_id=self.surface.session_id,
            artifact_hash=self.artifact.digest(),
            status=status,
            code=code,
            step=self.step,
            expected=self.expected,
            observed=json.dumps(
                {
                    "screens": [
                        h
                        for h in sorted(
                            {
                                self.artifact.entry_condition,
                                self.artifact.success_condition,
                                *(s.pre for s in self.artifact.steps),
                                *(s.post.text for s in self.artifact.steps),
                            }
                        )
                        if h and self.last_view and self.last_view.has(h)
                    ],
                    "modal": bool(self.last_view and self.last_view.modal),
                    "condition": code,
                }
            ),
            outputs=outputs,
            llm_calls=self.llm_calls,
            evidence_dir=str(self.evidence.directory.resolve()),
        )
        if self.last_view:
            self.evidence.screenshot(self.last_view.png, "final.png", self.last_view)
        self.evidence.event("run_finished", status=status, code=code, llm_calls=self.llm_calls)
        self.evidence.result(result)
        return result

    async def replay(self):
        try:
            await self.wait_condition(Condition(text=self.artifact.entry_condition))
            for step in self.artifact.steps:
                await self.execute_step(step)
            return self.finish("success", "REVIEW_READY", await self.extract())
        except StopRun as exc:
            return self.finish(exc.status, exc.code)
        except PolicyDenied as exc:
            return self.finish("failure", str(exc))
        except Exception as exc:
            # Never persist arbitrary exception messages (provider URLs or sensitive UI values).
            self.evidence.event("internal_error", error_type=type(exc).__name__)
            code = "SESSION_LOST" if not self.surface.is_alive() else "EXECUTION_ERROR"
            return self.finish("failure", code)
