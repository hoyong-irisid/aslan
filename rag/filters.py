from qdrant_client.http import models as rest

from rag.schemas import RagFilters


def qdrant_filter(f: RagFilters, *, exclude_partner: bool = False) -> rest.Filter | None:
    must: list[rest.FieldCondition] = []
    must_not: list[rest.FieldCondition] = []
    if f.product:
        must.append(rest.FieldCondition(key="product", match=rest.MatchValue(value=f.product)))
    if f.language:
        must.append(rest.FieldCondition(key="language", match=rest.MatchValue(value=f.language)))
    if f.doc_type:
        must.append(rest.FieldCondition(key="doc_type", match=rest.MatchValue(value=f.doc_type)))
    if f.department:
        must.append(
            rest.FieldCondition(key="department", match=rest.MatchValue(value=f.department))
        )
    if f.version:
        must.append(rest.FieldCondition(key="version", match=rest.MatchValue(value=f.version)))
    if f.access:
        must.append(rest.FieldCondition(key="access", match=rest.MatchValue(value=f.access)))
    if exclude_partner:
        must_not.append(
            rest.FieldCondition(key="access", match=rest.MatchValue(value="partner"))
        )
    if not must and not must_not:
        return None
    return rest.Filter(must=must or None, must_not=must_not or None)
