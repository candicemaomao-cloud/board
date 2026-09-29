from sqlalchemy.orm import Session

from app.models import Tag


def get_or_create_tags(db: Session, names: list[str]) -> list[Tag]:
    result: list[Tag] = []
    seen: set[str] = set()
    for raw in names:
        name = raw.strip()
        if not name or name in seen:
            continue
        seen.add(name)
        tag = db.query(Tag).filter(Tag.name == name).one_or_none()
        if tag is None:
            tag = Tag(name=name)
            db.add(tag)
            db.flush()
        result.append(tag)
    return result
