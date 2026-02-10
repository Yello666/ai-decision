from typing import Any, Type
from sqlalchemy.orm import Session


class BaseCRUD:
    def __init__(self, model: Type[Any]):
        self.model = model

    def get(self, db: Session, item_id: int):
        return db.query(self.model).filter(self.model.id == item_id).first()

    def list(self, db: Session, skip: int = 0, limit: int = 20):
        return db.query(self.model).offset(skip).limit(limit).all()

    def create(self, db: Session, obj_in: dict):
        db_obj = self.model(**obj_in)
        db.add(db_obj)
        db.commit()
        db.refresh(db_obj)
        return db_obj

    def update(self, db: Session, db_obj, obj_in: dict):
        for field, value in obj_in.items():
            setattr(db_obj, field, value)
        db.add(db_obj)
        db.commit()
        db.refresh(db_obj)
        return db_obj
