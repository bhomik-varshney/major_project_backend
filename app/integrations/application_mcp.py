"""Velion-style, bounded MCP SDK sessions over stdio. No browser-selected commands."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import sys
from tempfile import TemporaryFile


class MCPUnavailable(RuntimeError):
    def __init__(self,code,*,retryable=False):
        self.code=code;self.retryable=retryable
        super().__init__(code)


TOOLS = frozenset({'inspect_application', 'prefill_application','open_application','application_status','build_application_preview'})
URI = 'ui://ascendra/application'


def adapter_command(settings, provider):
    if provider == 'demo':return [sys.executable, '-m', 'app.mcp.application_server']
    if provider == 'configured' and settings.application_mcp_command:return settings.application_mcp_command
    raise MCPUnavailable('application_adapter_unavailable')


async def _session(settings, provider, operation, arguments,on_progress=None):
    try:
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client
        command = adapter_command(settings, provider)
        root = Path(__file__).resolve().parents[2]
        # The adapter receives only the approval packet, not the backend's API keys,
        # session secret or database settings. The SDK adds its minimal OS defaults.
        params = StdioServerParameters(command=sys.executable,
            args=['-m','app.mcp.launcher','--',*command], cwd=str(root),
            env={'PYTHONPATH':str(root),'PYTHONDONTWRITEBYTECODE':'1',
                 'PATH':os.environ.get('PATH','/usr/bin:/bin'),
                 'ASCENDRA_MCP_STATE_DIR':str((settings.data_dir/'application-mcp').resolve())})
        with TemporaryFile(mode='w+', encoding='utf-8') as errors:
            async with asyncio.timeout(settings.application_mcp_timeout_seconds):
                async with stdio_client(params, errlog=errors) as (read,write):
                    async with ClientSession(read,write) as session:
                        await session.initialize()
                        listing = await session.list_tools()
                        tools = [tool.model_dump(by_alias=True,exclude_none=True) for tool in listing.tools]
                        if operation == 'discover':return {'tools':tools,'provider':provider,'transport':'stdio'}
                        if operation == 'resource':
                            result = await session.read_resource(URI)
                            first = result.contents[0]
                            if not getattr(first,'mimeType','').startswith('text/html;profile=mcp-app'):
                                raise MCPUnavailable('application_resource_invalid')
                            html = getattr(first,'text','')
                            if not html.lower().lstrip().startswith('<!doctype html') or len(html)>2_000_000:
                                raise MCPUnavailable('application_resource_invalid')
                            return {'html':html,'mime_type':first.mimeType,'uri':URI}
                        if operation not in TOOLS or operation not in {t['name'] for t in tools}:
                            raise MCPUnavailable('application_tool_not_available')
                        result = await session.call_tool(operation,arguments,progress_callback=on_progress)
                        if result.isError:raise MCPUnavailable('application_tool_error')
                        payload = result.structuredContent
                        if payload is None:
                            payload = json.loads(next(block.text for block in result.content if block.type=='text'))
                        if not isinstance(payload,dict):raise MCPUnavailable('application_response_invalid')
                        if isinstance(payload.get('error'),dict):
                            error=payload['error'];allowed={'authentication_required','external_page_unavailable','unsupported_form','form_schema_changed','upload_failed','navigation_failed','external_session_expired','duplicate_application'}
                            code=error.get('code') if error.get('code') in allowed else 'application_tool_error'
                            raise MCPUnavailable(code,retryable=error.get('retryable') is True)
                        return {'result':payload,'tools':tools,'transport':'stdio'}
    except MCPUnavailable:raise
    except Exception as exc:
        # AnyIO wraps errors raised inside its stdio task groups. Preserve our
        # already-sanitized typed error after the SDK has cleaned up the process.
        def typed(error):
            if isinstance(error,MCPUnavailable):return error
            for child in getattr(error,'exceptions',[]):
                found=typed(child)
                if found:return found
            return None
        known=typed(exc)
        if known:raise known from None
        # No subprocess stderr or argument values in exceptions/logs.
        raise MCPUnavailable('application_mcp_timeout' if isinstance(exc,TimeoutError) else 'application_mcp_unavailable',retryable=True) from None


def call(settings,provider,operation,arguments=None,on_progress=None,on_retry=None):
    kwargs=(settings,provider,operation,arguments or {},on_progress)
    def once():
        try:asyncio.get_running_loop()
        except RuntimeError:return asyncio.run(_session(*kwargs))
        with ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(asyncio.run,_session(*kwargs)).result(timeout=settings.application_mcp_timeout_seconds+3)
    try:return once()
    except MCPUnavailable as exc:
        # Only read-only discovery/inspection may retry automatically. Writes
        # require explicit user retry plus validated completed-action evidence.
        if operation in {'discover','inspect_application','resource','application_status','build_application_preview'} and exc.retryable:
            if on_retry:on_retry()
            return once()
        raise


def preview(settings,draft,facts,job):
    """Only the owned local MCP preview app receives unapproved presentation data."""
    from app.schemas.applications import ApplicationDraft
    from app.schemas.application_preview import ApplicationPreview
    from app.services.application_transform import build_preview
    draft=ApplicationDraft.model_validate(draft)
    expected=build_preview(draft,facts,job)
    try:
        result=call(settings,'demo','build_application_preview',{'draft':draft.model_dump(exclude={'preview'}),'facts':facts,'job':job})
        actual=ApplicationPreview.model_validate(result['result']['preview'])
        if actual!=expected:raise MCPUnavailable('application_preview_invalid')
        return actual
    except (MCPUnavailable,ValueError,KeyError,TypeError):return build_preview(draft,facts,job,'local_fallback')
