"""券商报告解析子包。"""

from .pipeline import (  # noqa: F401
    ingest_pdf,
    list_reports,
    parse_report,
    read_record,
)
