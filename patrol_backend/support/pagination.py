"""Shared pagination helpers for support list endpoints."""


DEFAULT_PAGE_SIZE = 25
MAX_PAGE_SIZE = 100


def paginate_queryset(qs, request, *, default_page_size=DEFAULT_PAGE_SIZE, max_page_size=MAX_PAGE_SIZE):
    """
    Slice a queryset using ?page=&page_size=.

    Returns (page_qs, pagination_meta).
    """
    try:
        page = max(1, int(request.query_params.get("page") or 1))
    except (TypeError, ValueError):
        page = 1
    try:
        page_size = int(request.query_params.get("page_size") or default_page_size)
    except (TypeError, ValueError):
        page_size = default_page_size
    page_size = max(1, min(page_size, max_page_size))
    total = qs.count()
    start = (page - 1) * page_size
    end = start + page_size
    total_pages = (total + page_size - 1) // page_size if page_size else 0
    return qs[start:end], {
        "page": page,
        "page_size": page_size,
        "total": total,
        "total_pages": total_pages,
    }
