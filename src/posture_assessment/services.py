from __future__ import annotations

import hmac
import re
from dataclasses import dataclass
from datetime import date, datetime

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError

from posture_assessment.database import DatabaseManager, hash_password
from posture_assessment.models import AssessmentSession, Operator, User


class AppError(Exception):
    pass


class ValidationError(AppError):
    pass


class DuplicatePatientError(AppError):
    pass


class NotFoundError(AppError):
    pass


@dataclass
class UserInput:
    patient_no: str | None
    name: str
    sex: str
    birth_date: date
    mobile: str | None = None
    height_cm: float | None = None
    weight_kg: float | None = None
    address: str | None = None
    email: str | None = None
    notes: str | None = None


@dataclass(frozen=True, slots=True)
class PageResult:
    items: list[User]
    total: int
    page: int
    page_size: int

    @property
    def pages(self) -> int:
        return max(1, (self.total + self.page_size - 1) // self.page_size)


class AuthService:
    def __init__(self, db: DatabaseManager):
        self.db = db

    def authenticate(self, username: str, password: str) -> bool:
        if not username or not password:
            return False
        with self.db.session() as session:
            operator = session.scalar(
                select(Operator).where(
                    Operator.username == username.strip(), Operator.active.is_(True)
                )
            )
            if operator is None:
                return False
            candidate = hash_password(password, operator.salt)
            return hmac.compare_digest(candidate, operator.password_hash)


class UserService:
    PATIENT_NO_RE = re.compile(r"^[A-Za-z0-9_-]{2,32}$")
    MOBILE_RE = re.compile(r"^[0-9+() -]{6,24}$")
    EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")

    def __init__(self, db: DatabaseManager):
        self.db = db

    def generate_patient_no(self) -> str:
        prefix = f"P{date.today():%Y%m%d}"
        with self.db.session() as session:
            count = session.scalar(
                select(func.count(User.id)).where(User.patient_no.like(f"{prefix}%"))
            ) or 0
        return f"{prefix}{count + 1:03d}"

    def validate(self, data: UserInput, require_complete: bool) -> UserInput:
        patient_no = (data.patient_no or "").strip() or self.generate_patient_no()
        name = data.name.strip()
        sex = data.sex.strip()
        mobile = (data.mobile or "").strip() or None
        address = (data.address or "").strip() or None
        email = (data.email or "").strip() or None
        notes = (data.notes or "").strip() or None

        errors: list[str] = []
        if not self.PATIENT_NO_RE.fullmatch(patient_no):
            errors.append("患者编号只能包含字母、数字、下划线或短横线，长度 2-32 位")
        if not name:
            errors.append("姓名不能为空")
        if sex not in {"男", "女", "未说明"}:
            errors.append("性别必须为男、女或未说明")
        if not isinstance(data.birth_date, date) or data.birth_date > date.today():
            errors.append("出生日期无效")
        if mobile and not self.MOBILE_RE.fullmatch(mobile):
            errors.append("手机号码格式不正确")
        if email and not self.EMAIL_RE.fullmatch(email):
            errors.append("邮箱格式不正确")
        if data.height_cm is not None and not 80 <= data.height_cm <= 250:
            errors.append("身高应在 80-250 cm 之间")
        if data.weight_kg is not None and not 20 <= data.weight_kg <= 300:
            errors.append("体重应在 20-300 kg 之间")
        if require_complete:
            if not mobile:
                errors.append("手机号码不能为空")
            if data.height_cm is None:
                errors.append("身高不能为空")
            if data.weight_kg is None:
                errors.append("体重不能为空")
            if not address:
                errors.append("地址不能为空")
        if errors:
            raise ValidationError("\n".join(errors))

        return UserInput(
            patient_no=patient_no,
            name=name,
            sex=sex,
            birth_date=data.birth_date,
            mobile=mobile,
            height_cm=data.height_cm,
            weight_kg=data.weight_kg,
            address=address,
            email=email,
            notes=notes,
        )

    def create(self, data: UserInput, require_complete: bool = True) -> User:
        clean = self.validate(data, require_complete=require_complete)
        user = User(**clean.__dict__)
        try:
            with self.db.session() as session:
                session.add(user)
                session.flush()
                session.refresh(user)
        except IntegrityError as exc:
            raise DuplicatePatientError(f"患者编号 {clean.patient_no} 已存在") from exc
        return user

    def update(
        self, user_id: int, data: UserInput, require_complete: bool = True
    ) -> User:
        clean = self.validate(data, require_complete=require_complete)
        try:
            with self.db.session() as session:
                user = session.get(User, user_id)
                if user is None or user.deleted_at is not None:
                    raise NotFoundError("患者不存在或已归档")
                for field, value in clean.__dict__.items():
                    setattr(user, field, value)
                user.updated_at = datetime.now()
                session.flush()
                session.refresh(user)
        except IntegrityError as exc:
            raise DuplicatePatientError(f"患者编号 {clean.patient_no} 已存在") from exc
        return user

    def get(self, user_id: int, include_deleted: bool = False) -> User:
        with self.db.session() as session:
            user = session.get(User, user_id)
            if user is None or (user.deleted_at is not None and not include_deleted):
                raise NotFoundError("患者不存在或已归档")
            return user

    def find_by_patient_no(self, patient_no: str) -> User | None:
        with self.db.session() as session:
            return session.scalar(
                select(User).where(
                    User.patient_no == patient_no, User.deleted_at.is_(None)
                )
            )

    def search(
        self,
        patient_no: str = "",
        name: str = "",
        mobile: str = "",
        page: int = 1,
        page_size: int = 20,
    ) -> PageResult:
        filters = [User.deleted_at.is_(None)]
        if patient_no.strip():
            filters.append(User.patient_no.contains(patient_no.strip()))
        if name.strip():
            filters.append(User.name.contains(name.strip()))
        if mobile.strip():
            filters.append(User.mobile.contains(mobile.strip()))
        page = max(1, page)
        with self.db.session() as session:
            total = session.scalar(select(func.count(User.id)).where(*filters)) or 0
            items = list(
                session.scalars(
                    select(User)
                    .where(*filters)
                    .order_by(User.updated_at.desc(), User.id.desc())
                    .offset((page - 1) * page_size)
                    .limit(page_size)
                )
            )
        return PageResult(items=items, total=total, page=page, page_size=page_size)

    def archive(self, user_id: int) -> None:
        with self.db.session() as session:
            user = session.get(User, user_id)
            if user is None or user.deleted_at is not None:
                raise NotFoundError("患者不存在或已归档")
            user.deleted_at = datetime.now()

    def list_assessments(self, user_id: int) -> list[AssessmentSession]:
        with self.db.session() as session:
            return list(
                session.scalars(
                    select(AssessmentSession)
                    .where(AssessmentSession.user_id == user_id)
                    .order_by(AssessmentSession.started_at.desc())
                )
            )

    def start_assessment(self, user_id: int) -> AssessmentSession:
        user = self.get(user_id)
        missing: list[str] = []
        if not user.mobile:
            missing.append("手机号码")
        if user.height_cm is None:
            missing.append("身高")
        if user.weight_kg is None:
            missing.append("体重")
        if not user.address:
            missing.append("地址")
        if missing:
            raise ValidationError("开始检测前请补齐：" + "、".join(missing))
        session_no = f"AS{datetime.now():%Y%m%d%H%M%S%f}"
        assessment = AssessmentSession(
            session_no=session_no, user_id=user_id, assessment_type="体态检测"
        )
        with self.db.session() as session:
            session.add(assessment)
            session.flush()
            session.refresh(assessment)
        return assessment
