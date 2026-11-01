"""Pure response diagnostics. Does not send requests or read credentials."""
import re
from urllib.parse import urlsplit


def decode_html(body, content_type=""):
    # requests may assign ISO-8859-1 to text/html without a charset, even when
    # the actual document declares UTF-8. Prefer explicit response/document data.
    declared = re.search(r"charset\s*=\s*['\"]?([\w-]+)", content_type, re.I)
    if not declared:
        declared = re.search(rb"charset\s*=\s*['\"]?([\w-]+)", body[:4096], re.I)
    encoding = declared[1] if declared else "utf-8"
    if isinstance(encoding, bytes):
        encoding = encoding.decode("ascii", "replace")
    try:
        return body.decode(encoding)
    except (LookupError, UnicodeDecodeError):
        return body.decode("utf-8", "replace")


def diagnostic_snapshot(status_code, headers, body):
    headers = {k.lower(): v for k, v in headers.items()}
    text = decode_html(body, headers.get("content-type", ""))
    title = re.search(r"<title[^>]*>(.*?)</title>", text, re.S | re.I)
    # Retain safe public error text, never Set-Cookie, authorization, or scripts.
    safe = re.sub(r"<(script|style)\b[^>]*>.*?</\1>", "", text, flags=re.S | re.I)
    safe = re.sub(r"<[^>]+>", " ", safe)
    safe = " ".join(safe.split())
    # Diagnostic text can still contain reflected secrets: do not retain it if
    # credential-shaped keys appear. Content type and title remain sufficient.
    sensitive = re.search(r"sessdata|bili_jct|authorization|access_token|refresh_token|cookie", safe, re.I)
    snapshot = {
        "status_code": status_code,
        "page_title": title[1][:200] if title else None,
        "safe_headers": {k: headers[k] for k in ("content-type", "content-length", "retry-after") if k in headers},
        "error_text_excerpt": None if status_code == 200 or sensitive else safe[:800],
        "classification": "success" if status_code == 200 else "http_error_unclassified",
        "retry_automatically": False,
    }
    if status_code == 412:
        snapshot["classification"] = "http_412_precondition_or_site_policy_unknown"
    elif status_code in {401, 403}:
        snapshot["classification"] = "authentication_or_access_rejected"
    elif 300 <= status_code < 400:
        snapshot["classification"] = "redirect_not_followed"
        loc = urlsplit(headers.get("location", ""))
        snapshot["redirect_host"] = loc.hostname  # no signed query or fragment
    return snapshot


def interpret_existing(record):
    """Do not invent lost response headers/body or infer other modality failures."""
    return {
        "tested_operation": "anonymous_python_html_get",
        "target": record["target"],
        "http_status": record.get("status_code"),
        "html_result": "failed" if record.get("status_code") == 412 else record.get("state"),
        "root_cause": "unresolved_missing_response_body_and_headers",
        "redirect_setting_explains_412": False,
        "metadata_api": "not_tested",
        "subtitle": "not_tested",
        "storyboard": "not_tested",
        "media": "not_tested",
        "normal_browser": "separate_test_required",
        "conclusion": "不能将 HTML 请求的 412 推广为所有 B站获取方式失败；也不能据此证明换库或登录必然成功。",
    }
