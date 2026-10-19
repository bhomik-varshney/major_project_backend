"""Additive evidence-rich view; legacy profile values and endpoints remain usable."""
from app.schemas.extraction import CandidateDraft, FieldEvidence, StructuredResume
from app.services.resume_structure import leaves


def review_view(values, evidence, status, applied_fields=(), method="deterministic_limited", warnings=()):
    values = CandidateDraft.model_validate(values).model_dump()
    expanded = {}

    def fact(value, path):
        proof = FieldEvidence.model_validate(evidence.get(path, {})).model_dump()
        found = bool(value)
        confirmed = found and status == "verified" and path.split(".")[0] in applied_fields
        edited = proof["edited"] or proof["edited_by_user"] or proof["method"] == "user"
        proof.update(verified=confirmed, edited_by_user=edited,
            classification=proof["classification"] if found else "not_found",
            review_status="confirmed" if confirmed else "not_detected" if not found else "not_applied" if status == "verified" else "pending")
        if found and not proof["source"]:
            proof["classification"] = "ambiguous"
            proof["warning"] = proof["warning"] or "No source evidence is available. Confirm this candidate-entered or legacy value explicitly."
        if not found:
            proof.update(source=None, confidence="unknown", warning="Not detected in this document.")
        expanded[path] = proof
        return {"value": value, "evidence": proof["source"], "confidence": proof["confidence"],
            "classification": proof["classification"], "method": proof["method"], "review_status": proof["review_status"],
            "edited_by_user": edited, "verified": confirmed, "original_text": proof["original_text"] or value,
            "source_section": proof["source_section"], "heading": proof["heading"],
            "statement_type": proof["statement_type"], "warning": proof["warning"]}

    def wrap(value, path):
        if isinstance(value, dict):
            return {key: wrap(child, f"{path}.{key}") for key, child in value.items()}
        if isinstance(value, list):
            return [wrap(child, f"{path}.{i}") for i, child in enumerate(value)]
        return fact(value, path)

    statement = fact(values["career_objective"], "career_objective")
    structured = {"personal_info": {key: fact(values[key], key) for key in ("name", "email", "phone", "location")},
        "profile_statement": {"text": statement, "heading": statement["heading"], "type": statement["statement_type"] or "not_found"},
        **{field: wrap(values[field], field) for field in ("education", "skills", "experience", "projects", "certifications", "achievements", "links")},
        "warnings": list(warnings), "extraction_metadata": {"parser_version": "2" if any(p.get("parser_version") == "2" for p in evidence.values()) else "legacy" if evidence else "unknown", "method": method,
            "reading_order_uncertain": any("column" in warning.lower() for warning in warnings)}}
    # Optional legacy preference evidence still participates in the same review rules.
    for path, value in leaves(values).items():
        if path not in expanded:
            fact(value, path)
    StructuredResume.model_validate(structured)
    skills = [{"name": skill["value"], "source": "manual" if skill["edited_by_user"] else "resume",
        **skill, "user_confirmed": skill["verified"]} for skill in structured["skills"]]
    return structured, expanded, skills


def proposal_from_structured(structured):
    """Provider metadata is untrusted: only real anchors are accepted downstream."""
    from app.schemas.extraction import AIProposal
    values, claims = {}, []
    def unpack(value, path):
        if isinstance(value, list):
            return [unpack(child, f"{path}.{i}") for i, child in enumerate(value)]
        if "value" not in value:
            return {key: unpack(child, f"{path}.{key}") for key, child in value.items()}
        source = value["evidence"]
        if value["value"] and source:
            claims.append({"path": path, "block_id": source["block_id"], "quote": source["quote"],
                "line": source.get("line"), "confidence": value["confidence"], "warning": value["warning"],
                "classification": value["classification"], "inferred": value["classification"] == "not_found"})
        return value["value"]
    raw = structured.model_dump()
    for field, value in raw["personal_info"].items():
        values[field] = unpack(value, field)
    values["career_objective"] = unpack(raw["profile_statement"]["text"], "career_objective")
    for field in ("education", "skills", "experience", "projects", "certifications", "achievements", "links"):
        values[field] = unpack(raw[field], field)
    return AIProposal.model_validate({"values": values, "claims": claims})
