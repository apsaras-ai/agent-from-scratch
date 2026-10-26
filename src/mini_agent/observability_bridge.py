"""Chapter 22: privacy-first tracing bridge for Chapters 4 and 6.

The gateway may receive full model messages and return full model content;
ONLY provider/model labels, usage and safe error states reach TraceRecorder.
Tool arguments and results remain entirely outside the trace store.

No network call is added. Real ModelBackend may contact remote APIs only
if explicitly configured by the host; default chapter demo uses a fake one.
"""
from __future__ import annotations

from typing import Any, Mapping, Protocol

from .agent_v2 import ModelReply, ToolCall, ToolRegistry
from .model_gateway import GatewayResponse, ModelBackend, TokenUsage
from .observability import RateCard, TraceRecorder, TraceError, _LABEL


class TracedGatewayModel:
    """Chapter 4 AgentKernel-compatible Model wrapper.

    Calls a Chapter 6 ModelBackend and converts GatewayResponse to ModelReply.
    If usage counts are missing, cost is UNKNOWN, never fabricated as zero.
    """

    def __init__(
        self, backend: ModelBackend, tracer: TraceRecorder,
        *, provider: str, model: str,
        rate_card: RateCard | None = None,
    ) -> None:
        if not callable(getattr(backend, "complete", None)):
            raise TraceError("backend_requires_complete")
        if any(not isinstance(x, str) or not _LABEL.fullmatch(x)
               for x in (provider, model)):
            raise TraceError("invalid_provider_model_labels")
        if rate_card is not None and (
            rate_card.provider != provider or rate_card.model != model
        ):
            raise TraceError("rate_card_model_mismatch")
        self.backend = backend
        self.tracer = tracer
        self.provider = provider
        self.model = model
        self.rate_card = rate_card

    def complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> ModelReply:
        with self.tracer.span("chat", attributes={
            "gen_ai.provider.name": self.provider,
            "gen_ai.request.model": self.model,
        }) as span:
            response = self.backend.complete(messages, tools)
            if (not isinstance(response, GatewayResponse)
                    or not isinstance(response.reply, ModelReply)
                    or response.provider != self.provider
                    or response.model != self.model
                    or not isinstance(response.usage, TokenUsage)):
                raise TraceError("invalid_gateway_response")
            usage = response.usage
            if usage.input_tokens is not None and usage.output_tokens is not None:
                span.record_usage(
                    provider=self.provider, model=self.model,
                    input_tokens=usage.input_tokens,
                    output_tokens=usage.output_tokens,
                    rate_card=self.rate_card,
                )
            # No model reply text, system prompt or tool argument stored.
            return response.reply


class TracedToolRegistry:
    """Intercept Chapter 4 ToolRegistry.invoke without bypassing its policy."""

    def __init__(self, tools: ToolRegistry, tracer: TraceRecorder) -> None:
        if not isinstance(tools, ToolRegistry):
            raise TraceError("real_tool_registry_required")
        self.tools = tools
        self.tracer = tracer

    def descriptions(self) -> list[dict[str, Any]]:
        return self.tools.descriptions()

    def invoke(self, call: ToolCall, *, allow_writes: bool = False) -> dict[str, Any]:
        # Tool names remain strictly bounded labels; never log arguments_json.
        if not isinstance(call, ToolCall) or not _LABEL.fullmatch(call.name):
            raise TraceError("invalid_tool_call_label")
        with self.tracer.span("execute_tool", attributes={
            "gen_ai.tool.name": call.name,
        }) as span:
            observed = self.tools.invoke(call, allow_writes=allow_writes)
            if not observed.get("ok"):
                # Do not record arbitrary exception messages or tool results.
                span.fail("tool_rejected_or_failed")
            return observed
