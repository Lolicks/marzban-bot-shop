from __future__ import annotations

import secrets
from typing import Any
from datetime import datetime, timedelta
from decimal import Decimal

import re
from sqlalchemy import func, or_, select, update, delete, and_, case
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from .models import (
    BalanceLog,
    PaymentLog,
    PaymentStatus,
    Plan,
    PromoCode,
    ReferralClick,
    ReferralEvent,
    ReferralReward,
    Referral,
    TelegramUser,
    UserRole,
    UserStatus,
    VpnAccount,
    NewsPost,
    DesktopLoginCode,
    DesktopTrialDevice,
    FeedbackEntry,
)


def slugify(text: str) -> str:
    s = (text or "").strip().lower()
    s = re.sub(r"[^a-z0-9а-яё]+", "-", s, flags=re.IGNORECASE)
    s = re.sub(r"-{2,}", "-", s).strip("-")
    return s or "post"


def _promo_used_clause() -> Any:
    return and_(
        PaymentLog.note.is_not(None),
        PaymentLog.note.ilike("promo:%"),
        ~PaymentLog.note.ilike("promo:;%"),
    )


def _non_promo_clause() -> Any:
    return or_(
        PaymentLog.note.is_(None),
        ~PaymentLog.note.ilike("promo:%"),
        PaymentLog.note.ilike("promo:;%"),
    )


def _non_balance_clause() -> Any:
    return or_(
        PaymentLog.method.is_(None),
        func.lower(PaymentLog.method) != "balance",
    )


async def list_news_admin(session: AsyncSession, limit: int = 200) -> list[NewsPost]:
    result = await session.execute(select(NewsPost).order_by(NewsPost.created_at.desc()).limit(limit))
    return list(result.scalars().all())


async def list_published_news(session: AsyncSession, limit: int = 50) -> list[NewsPost]:
    result = await session.execute(
        select(NewsPost)
        .where(NewsPost.is_published.is_(True))
        .order_by((NewsPost.published_at.desc().nullslast()), NewsPost.created_at.desc())
        .limit(limit)
    )
    return list(result.scalars().all())


async def get_news_by_slug(session: AsyncSession, slug: str) -> NewsPost | None:
    result = await session.execute(select(NewsPost).where(NewsPost.slug == slug))
    return result.scalar_one_or_none()


async def get_news_by_id(session: AsyncSession, post_id: int) -> NewsPost | None:
    result = await session.execute(select(NewsPost).where(NewsPost.id == post_id))
    return result.scalar_one_or_none()


async def create_news_post(
    session: AsyncSession,
    title: str,
    slug: str | None,
    content_md: str,
    is_published: bool,
    published_at: datetime | None,
    image_url: str | None = None,
    mini_enabled: bool = False,
    mini_only: bool = False,
    mini_days: int = 0,
    mini_start_at: datetime | None = None,
) -> NewsPost:
    base_slug = slugify(slug or title)
    unique = base_slug
    i = 2
    while await get_news_by_slug(session, unique):
        unique = f"{base_slug}-{i}"
        i += 1

    post = NewsPost(
        title=(title or "").strip()[:200] or "Untitled",
        slug=unique[:200],
        content_md=content_md or "",
        image_url=(image_url or "").strip() or None,
        is_published=bool(is_published),
        mini_enabled=bool(mini_enabled),
        mini_only=bool(mini_only),
        mini_days=int(mini_days or 0),
        mini_start_at=mini_start_at,
        published_at=published_at if is_published else None,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    session.add(post)
    await session.commit()
    await session.refresh(post)
    return post


async def update_news_post(
    session: AsyncSession,
    post: NewsPost,
    title: str,
    slug: str,
    content_md: str,
    is_published: bool,
    published_at: datetime | None,
    image_url: str | None = None,
    mini_enabled: bool | None = None,
    mini_only: bool | None = None,
    mini_days: int | None = None,
    mini_start_at: datetime | None = None,
) -> NewsPost:
    post.title = (title or "").strip()[:200] or post.title
    new_slug = slugify(slug or post.title)[:200]
    if new_slug != post.slug:
        unique = new_slug
        i = 2
        while True:
            other = await get_news_by_slug(session, unique)
            if not other or other.id == post.id:
                break
            unique = f"{new_slug}-{i}"
            i += 1
        post.slug = unique
    post.content_md = content_md or ""
    if image_url is not None:
        post.image_url = (image_url or "").strip() or None
    post.is_published = bool(is_published)
    if mini_enabled is not None:
        post.mini_enabled = bool(mini_enabled)
    if mini_only is not None:
        post.mini_only = bool(mini_only)
    if mini_days is not None:
        post.mini_days = int(mini_days or 0)
    if mini_start_at is not None or mini_enabled is not None:
        post.mini_start_at = mini_start_at
    post.published_at = published_at if post.is_published else None
    post.updated_at = datetime.utcnow()
    await session.commit()
    await session.refresh(post)
    return post


async def get_active_mini_news(session: AsyncSession) -> NewsPost | None:
    now = datetime.utcnow()
    result = await session.execute(
        select(NewsPost)
        .where(NewsPost.mini_enabled.is_(True))
        .order_by((NewsPost.mini_start_at.desc().nullslast()), NewsPost.created_at.desc())
        .limit(5)
    )
    posts = list(result.scalars().all())
    for p in posts:
        start = p.mini_start_at or p.created_at
        days = int(p.mini_days or 0)
        if days > 0 and start:
            if start + timedelta(days=days) < now:
                continue
        return p
    return None


async def delete_news_post(session: AsyncSession, post_id: int) -> None:
    await session.execute(delete(NewsPost).where(NewsPost.id == post_id))
    await session.commit()


def _ref_code() -> str:
    return secrets.token_urlsafe(6).replace("-", "A").replace("_", "B")


async def get_or_create_user(
    session: AsyncSession,
    telegram_id: int,
    username: str | None,
    full_name: str | None,
    language: str = "en",
    referred_by_code: str | None = None,
    ref_source: str | None = None,
) -> TelegramUser:
    result = await session.execute(select(TelegramUser).where(TelegramUser.telegram_id == telegram_id))
    user = result.scalar_one_or_none()
    if user:
        user.username = username
        user.full_name = full_name
        if language in {"en", "ru"}:
            user.language = language
        await session.commit()
        await session.refresh(user)
        return user

    referred_by_id = None
    referred_by_user = None
    if referred_by_code:
        ref_result = await session.execute(select(TelegramUser).where(TelegramUser.referral_code == referred_by_code))
        ref_user = ref_result.scalar_one_or_none()
        if ref_user:
            referred_by_id = ref_user.id
            referred_by_user = ref_user

    user = TelegramUser(
        telegram_id=telegram_id,
        username=username,
        full_name=full_name,
        language=language if language in {"en", "ru"} else "en",
        role=UserRole.USER.value,
        status=UserStatus.ACTIVE.value,
        referred_by_id=referred_by_id,
        referral_code=_ref_code(),
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)
    if referred_by_id:
        await record_referral_event(
            session=session,
            referrer_id=referred_by_id,
            referred_id=user.id,
            referral_code=(referred_by_user.referral_code if referred_by_user else referred_by_code),
            event_type="signup",
            source=ref_source or "unknown",
        )
    return user


async def attach_referrer_once(
    session: AsyncSession,
    user: TelegramUser,
    referrer_id: int | None,
    source: str = "telegram",
) -> bool:
    if not referrer_id:
        return False
    if user.referred_by_id:
        return False
    if referrer_id == user.id:
        return False
    referrer = await find_user_by_id(session, referrer_id)
    if not referrer:
        return False
    user.referred_by_id = referrer_id
    await session.commit()
    await session.refresh(user)
    await record_referral_event(
        session=session,
        referrer_id=referrer_id,
        referred_id=user.id,
        referral_code=None,
        event_type="start",
        source=source,
    )
    return True


async def ensure_superadmin(session: AsyncSession, telegram_id: int) -> None:
    result = await session.execute(select(TelegramUser).where(TelegramUser.telegram_id == telegram_id))
    user = result.scalar_one_or_none()
    if not user:
        user = TelegramUser(
            telegram_id=telegram_id,
            username=None,
            full_name="Super Admin",
            role=UserRole.SUPERADMIN.value,
            referral_code=_ref_code(),
        )
        session.add(user)
    else:
        user.role = UserRole.SUPERADMIN.value
    await session.commit()


async def create_default_plans(session: AsyncSession) -> None:
    plans = await session.execute(select(Plan))
    if plans.scalars().first():
        return
    session.add_all(
        [
            Plan(name="Starter 30d", duration_days=30, traffic_gb=100, price_usd=Decimal("399.00"), price_stars=0, protocols_json=""),
            Plan(name="Pro 30d", duration_days=30, traffic_gb=300, price_usd=Decimal("699.00"), price_stars=0, protocols_json=""),
            Plan(name="Ultra 90d", duration_days=90, traffic_gb=1000, price_usd=Decimal("1990.00"), price_stars=0, protocols_json=""),
        ]
    )
    await session.commit()


async def list_active_plans(session: AsyncSession) -> list[Plan]:
    result = await session.execute(select(Plan).where(Plan.is_active.is_(True)).order_by(Plan.price_usd.asc()))
    return list(result.scalars().all())


async def get_plan(session: AsyncSession, plan_id: int) -> Plan | None:
    result = await session.execute(select(Plan).where(Plan.id == plan_id))
    return result.scalar_one_or_none()


async def repair_legacy_vpn_account_labels(session: AsyncSession) -> int:
    """
    Older versions stored labels as "Plan#<plan_id> payment#<payment_id>".
    We want to show admin-defined plan names everywhere (bot/site/mini/desktop),
    so we convert those legacy labels to Plan.name in the database.

    Returns number of updated VpnAccount rows.
    """
    # Narrow down candidates quickly using LIKE first.
    # NOTE: use plain LIKE for best cross-db compatibility (SQLite doesn't support ILIKE).
    rows = await session.execute(
        select(VpnAccount)
        .where(VpnAccount.label.is_not(None))
        .where(VpnAccount.label.like("Plan#% payment#%"))
        .limit(20000)
    )
    accounts = list(rows.scalars().all())
    if not accounts:
        return 0

    rx = re.compile(r"^Plan#(?P<plan_id>\\d+)\\s+payment#(?P<payment_id>\\d+)$")
    plan_ids: set[int] = set()
    parsed: list[tuple[VpnAccount, int]] = []
    for acc in accounts:
        m = rx.match((acc.label or "").strip())
        if not m:
            continue
        try:
            pid = int(m.group("plan_id"))
        except Exception:
            continue
        plan_ids.add(pid)
        parsed.append((acc, pid))

    if not parsed or not plan_ids:
        return 0

    plan_rows = await session.execute(select(Plan).where(Plan.id.in_(sorted(plan_ids))))
    plan_map = {p.id: p for p in plan_rows.scalars().all()}

    updated = 0
    for acc, pid in parsed:
        plan = plan_map.get(pid)
        if not plan or not (plan.name or "").strip():
            continue
        new_label = (plan.name or "").strip()[:64] or None
        if new_label and acc.label != new_label:
            acc.label = new_label
            updated += 1

    if updated:
        await session.commit()
    return updated


async def create_payment(
    session: AsyncSession,
    user_id: int,
    plan_id: int | None,
    amount_usd: Decimal,
    currency: str,
    method: str,
    tx_hash: str | None = None,
    note: str | None = None,
    amount_stars: int | None = None,
    balance_used_rub: Decimal | None = None,
) -> PaymentLog:
    payment = PaymentLog(
        user_id=user_id,
        plan_id=plan_id,
        amount_usd=amount_usd,
        balance_used_rub=Decimal(balance_used_rub or 0),
        amount_stars=int(amount_stars or 0),
        currency=currency,
        method=method,
        status=PaymentStatus.PENDING.value,
        tx_hash=tx_hash,
        note=note,
    )
    session.add(payment)
    await session.commit()
    await session.refresh(payment)
    return payment


async def confirm_payment(session: AsyncSession, payment_id: int) -> PaymentLog | None:
    result = await session.execute(select(PaymentLog).where(PaymentLog.id == payment_id))
    payment = result.scalar_one_or_none()
    if not payment:
        return None
    if payment.status == PaymentStatus.CONFIRMED.value:
        # Idempotency guard: already confirmed, caller must not reprovision.
        return None
    payment.status = PaymentStatus.CONFIRMED.value
    payment.confirmed_at = datetime.utcnow()
    await session.commit()
    await session.refresh(payment)
    return payment


async def list_pending_payments(session: AsyncSession) -> list[PaymentLog]:
    result = await session.execute(
        select(PaymentLog).where(PaymentLog.status == PaymentStatus.PENDING.value).order_by(PaymentLog.created_at.asc())
    )
    return list(result.scalars().all())


async def find_user_by_tg(session: AsyncSession, telegram_id: int) -> TelegramUser | None:
    result = await session.execute(select(TelegramUser).where(TelegramUser.telegram_id == telegram_id))
    return result.scalar_one_or_none()


async def find_user_by_id(session: AsyncSession, user_id: int) -> TelegramUser | None:
    result = await session.execute(select(TelegramUser).where(TelegramUser.id == user_id))
    return result.scalar_one_or_none()


async def find_user_by_referral_code(session: AsyncSession, code: str) -> TelegramUser | None:
    if not code:
        return None
    result = await session.execute(select(TelegramUser).where(TelegramUser.referral_code == code.strip()))
    return result.scalar_one_or_none()


async def set_user_role(session: AsyncSession, user_id: int, role: UserRole) -> None:
    await session.execute(update(TelegramUser).where(TelegramUser.id == user_id).values(role=role.value))
    await session.commit()


async def set_user_status(session: AsyncSession, user_id: int, status: UserStatus) -> None:
    await session.execute(update(TelegramUser).where(TelegramUser.id == user_id).values(status=status.value))
    await session.commit()


async def list_users(session: AsyncSession, limit: int = 500) -> list[TelegramUser]:
    result = await session.execute(select(TelegramUser).order_by(TelegramUser.created_at.desc()).limit(limit))
    return list(result.scalars().all())


async def list_users_advanced(
    session: AsyncSession,
    limit: int = 1000,
    query: str | None = None,
    status: str | None = None,
) -> list[TelegramUser]:
    stmt = select(TelegramUser)
    if status and status != "all":
        stmt = stmt.where(TelegramUser.status == status)
    if query:
        q = f"%{query.strip()}%"
        conds = [TelegramUser.username.ilike(q), TelegramUser.full_name.ilike(q)]
        if query.isdigit():
            conds.append(TelegramUser.telegram_id == int(query))
        stmt = stmt.where(or_(*conds))
    stmt = stmt.order_by(TelegramUser.created_at.desc()).limit(limit)
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def create_vpn_account(
    session: AsyncSession,
    user_id: int,
    marzban_username: str,
    label: str | None = None,
    is_trial: bool = False,
    plan_id: int | None = None,
) -> VpnAccount:
    account = VpnAccount(
        user_id=user_id,
        marzban_username=marzban_username,
        label=(label or "")[:64] or None,
        is_trial=is_trial,
        plan_id=plan_id,
        status=UserStatus.ACTIVE.value,
    )
    session.add(account)
    # Do not commit here. Provisioning can fail later (Marzban/network/etc) and we want a rollback
    # to remove the half-created record.
    await session.flush()
    await session.refresh(account)
    return account


async def get_vpn_account_by_username(session: AsyncSession, marzban_username: str) -> VpnAccount | None:
    result = await session.execute(select(VpnAccount).where(VpnAccount.marzban_username == marzban_username))
    return result.scalar_one_or_none()


async def get_vpn_account(session: AsyncSession, account_id: int) -> VpnAccount | None:
    result = await session.execute(select(VpnAccount).where(VpnAccount.id == account_id))
    return result.scalar_one_or_none()


async def list_user_vpn_accounts(session: AsyncSession, user_id: int, limit: int = 100) -> list[VpnAccount]:
    result = await session.execute(
        select(VpnAccount).where(VpnAccount.user_id == user_id).order_by(VpnAccount.created_at.desc()).limit(limit)
    )
    return list(result.scalars().all())


async def extend_vpn_account_local(session: AsyncSession, account: VpnAccount, days: int, traffic_gb: int) -> VpnAccount:
    now = datetime.utcnow()
    base = account.expire_at if account.expire_at and account.expire_at > now else now
    account.expire_at = base + timedelta(days=days)
    account.traffic_limit_gb = (account.traffic_limit_gb or 0) + max(traffic_gb, 0)
    account.status = UserStatus.ACTIVE.value
    await session.commit()
    await session.refresh(account)
    return account


async def sync_user_from_accounts(session: AsyncSession, user: TelegramUser) -> TelegramUser:
    accounts = await list_user_vpn_accounts(session, user.id, limit=500)
    if not accounts:
        user.expire_at = None
        user.subscription_url = None
        user.traffic_limit_gb = 0
        user.traffic_used_gb = 0
        user.marzban_username = None
        await session.commit()
        await session.refresh(user)
        return user

    active = [a for a in accounts if a.status == UserStatus.ACTIVE.value]
    source = active[0] if active else accounts[0]
    max_expire = max((a.expire_at for a in accounts if a.expire_at), default=None)

    user.expire_at = max_expire
    user.subscription_url = source.subscription_url
    user.traffic_limit_gb = sum(int(a.traffic_limit_gb or 0) for a in accounts)
    user.traffic_used_gb = sum(int(a.traffic_used_gb or 0) for a in accounts)
    user.marzban_username = source.marzban_username
    user.reminder_1d_sent = False
    user.reminder_3d_sent = False
    await session.commit()
    await session.refresh(user)
    return user


async def vpn_accounts_count_map(session: AsyncSession, user_ids: list[int]) -> dict[int, int]:
    if not user_ids:
        return {}
    rows = await session.execute(
        select(VpnAccount.user_id, func.count(VpnAccount.id))
        .where(VpnAccount.user_id.in_(user_ids))
        .group_by(VpnAccount.user_id)
    )
    return {int(uid): int(count) for uid, count in rows.all()}


def days_left(user: TelegramUser) -> int | None:
    if not user.expire_at:
        return None
    return max(0, int((user.expire_at - datetime.utcnow()).total_seconds() // 86400))


async def extend_subscription_local(session: AsyncSession, user: TelegramUser, days: int, traffic_gb: int) -> TelegramUser:
    now = datetime.utcnow()
    base = user.expire_at if user.expire_at and user.expire_at > now else now
    user.expire_at = base + timedelta(days=days)
    user.traffic_limit_gb = (user.traffic_limit_gb or 0) + max(traffic_gb, 0)
    user.status = UserStatus.ACTIVE.value
    user.reminder_1d_sent = False
    user.reminder_3d_sent = False
    await session.commit()
    await session.refresh(user)
    return user


async def mark_expired_users(session: AsyncSession) -> list[TelegramUser]:
    now = datetime.utcnow()
    result = await session.execute(
        select(TelegramUser).where(
            TelegramUser.expire_at.is_not(None),
            TelegramUser.expire_at < now,
            TelegramUser.status == UserStatus.ACTIVE.value,
        )
    )
    users = list(result.scalars().all())
    for user in users:
        user.status = UserStatus.EXPIRED.value
    await session.commit()
    return users


async def mark_expired_vpn_accounts(session: AsyncSession) -> list[VpnAccount]:
    now = datetime.utcnow()
    result = await session.execute(
        select(VpnAccount).where(
            VpnAccount.expire_at.is_not(None),
            VpnAccount.expire_at < now,
            VpnAccount.status == UserStatus.ACTIVE.value,
        )
    )
    accounts = list(result.scalars().all())
    for account in accounts:
        account.status = UserStatus.EXPIRED.value
    await session.commit()
    return accounts


async def apply_promo(session: AsyncSession, code: str) -> PromoCode | None:
    normalized = code.upper().strip()
    now = datetime.utcnow()
    upd = (
        update(PromoCode)
        .where(
            PromoCode.code == normalized,
            PromoCode.active.is_(True),
            or_(PromoCode.expires_at.is_(None), PromoCode.expires_at > now),
            PromoCode.used_count < PromoCode.max_uses,
        )
        .values(used_count=PromoCode.used_count + 1)
    )
    res = await session.execute(upd)
    if not res.rowcount:
        await session.rollback()
        return None
    await session.commit()
    result = await session.execute(select(PromoCode).where(PromoCode.code == normalized))
    return result.scalar_one_or_none()


async def create_feedback(
    session: AsyncSession,
    telegram_id: int,
    username: str | None,
    rating: int,
    message: str | None,
    source: str,
) -> FeedbackEntry:
    entry = FeedbackEntry(
        telegram_id=int(telegram_id),
        username=(username or None),
        rating=max(1, min(5, int(rating or 5))),
        message=(message or None),
        source=(source or "account")[:32],
    )
    session.add(entry)
    await session.commit()
    await session.refresh(entry)
    return entry


async def list_feedback(session: AsyncSession, limit: int = 200) -> list[FeedbackEntry]:
    rows = await session.execute(select(FeedbackEntry).order_by(FeedbackEntry.created_at.desc()).limit(limit))
    return list(rows.scalars().all())


async def list_feedback_for_site(session: AsyncSession, limit: int = 6) -> list[FeedbackEntry]:
    rows = await session.execute(
        select(FeedbackEntry)
        .where(FeedbackEntry.show_on_site.is_(True))
        .order_by(FeedbackEntry.created_at.desc())
        .limit(limit)
    )
    return list(rows.scalars().all())


async def set_feedback_visibility(session: AsyncSession, entry_id: int, show: bool) -> bool:
    row = await session.get(FeedbackEntry, entry_id)
    if not row:
        return False
    row.show_on_site = bool(show)
    await session.commit()
    return True


async def get_promo_by_code(session: AsyncSession, code: str) -> PromoCode | None:
    result = await session.execute(select(PromoCode).where(PromoCode.code == code.upper().strip()))
    return result.scalar_one_or_none()


def promo_effective_price(base_price: Decimal, promo: PromoCode | None) -> Decimal:
    if not promo:
        return Decimal(base_price)
    discount = Decimal(max(0, min(promo.discount_percent, 100))) / Decimal(100)
    discounted = Decimal(base_price) * (Decimal(1) - discount)
    if discounted < Decimal("0.00"):
        discounted = Decimal("0.00")
    return discounted.quantize(Decimal("0.01"))


async def ensure_referral_reward(session: AsyncSession, referrer_id: int, referred_id: int, bonus_days: int) -> bool:
    existing = await session.execute(
        select(ReferralReward).where(
            ReferralReward.referrer_id == referrer_id,
            ReferralReward.referred_id == referred_id,
        )
    )
    if existing.scalar_one_or_none():
        return False
    session.add(ReferralReward(referrer_id=referrer_id, referred_id=referred_id, bonus_days=bonus_days))
    await session.commit()
    return True


async def grant_referral_cash_bonus(
    session: AsyncSession,
    referrer_id: int,
    referred_id: int,
    amount_rub: Decimal,
    source: str = "unknown",
) -> bool:
    if amount_rub <= 0:
        return False
    created = await ensure_referral_reward(session, referrer_id, referred_id, bonus_days=0)
    if not created:
        return False
    referrer = await find_user_by_id(session, referrer_id)
    if referrer:
        await change_user_balance(
            session=session,
            user=referrer,
            amount_usd=Decimal(amount_rub),
            reason=f"referral_bonus_referrer_{referred_id}",
        )
    await record_referral_event(
        session=session,
        referrer_id=referrer_id,
        referred_id=referred_id,
        referral_code=None,
        event_type="reward",
        source=source,
        meta=f"+{amount_rub:.2f} to referrer",
    )
    return True


def _hash_ip(ip: str | None) -> str | None:
    if not ip:
        return None
    ip = ip.strip()
    if not ip:
        return None
    # Lightweight hashing to avoid storing raw IPs.
    import hashlib

    return hashlib.sha256(ip.encode("utf-8")).hexdigest()[:32]


async def create_referral_record(
    session: AsyncSession,
    referrer_id: int,
    referred_id: int,
    ip: str | None = None,
    user_agent: str | None = None,
) -> bool:
    existing = await session.execute(
        select(Referral).where(
            Referral.referrer_id == referrer_id,
            Referral.referred_id == referred_id,
        )
    )
    if existing.scalar_one_or_none():
        return False
    ip_hash = _hash_ip(ip)
    row = Referral(
        referrer_id=referrer_id,
        referred_id=referred_id,
        status="pending",
        reward_due_at=datetime.utcnow() + timedelta(hours=24),
        reward_given=False,
        ip_hash=ip_hash,
        user_agent=(user_agent or "")[:500] or None,
    )
    session.add(row)
    await session.commit()
    return True


async def process_referral_rewards(session: AsyncSession) -> int:
    now = datetime.utcnow()
    rows = await session.execute(
        select(Referral).where(
            Referral.reward_given.is_(False),
            Referral.reward_due_at <= now,
        )
    )
    rewards = list(rows.scalars().all())
    if not rewards:
        return 0
    processed = 0
    for reward in rewards:
        if reward.status == "suspicious":
            continue
        # Mark suspicious if IP was used in other referrals for different referrers.
        if reward.ip_hash:
            dup = await session.execute(
                select(Referral).where(
                    Referral.ip_hash == reward.ip_hash,
                    Referral.referrer_id != reward.referrer_id,
                )
            )
            if dup.scalar_one_or_none():
                reward.status = "suspicious"
                continue
        ok = await grant_referral_cash_bonus(
            session=session,
            referrer_id=reward.referrer_id,
            referred_id=reward.referred_id,
            amount_rub=Decimal("25.00"),
            source="purchase",
        )
        if ok:
            reward.reward_given = True
            reward.status = "approved"
            processed += 1
    await session.commit()
    return processed


async def record_referral_click(
    session: AsyncSession,
    referral_code: str,
    ip: str | None = None,
    user_agent: str | None = None,
    path: str | None = None,
) -> bool:
    code = (referral_code or "").strip()
    if not code:
        return False
    ref_result = await session.execute(select(TelegramUser).where(TelegramUser.referral_code == code))
    ref_user = ref_result.scalar_one_or_none()
    if not ref_user:
        return False
    session.add(
        ReferralClick(
            referrer_id=ref_user.id,
            referral_code=code,
            ip=(ip or "")[:64] or None,
            user_agent=(user_agent or "")[:500] or None,
            path=(path or "")[:255] or None,
        )
    )
    await session.commit()
    return True


async def record_referral_event(
    session: AsyncSession,
    referrer_id: int | None,
    referred_id: int | None,
    referral_code: str | None,
    event_type: str,
    source: str = "unknown",
    meta: str | None = None,
) -> bool:
    if not referrer_id and referral_code:
        ref_result = await session.execute(select(TelegramUser).where(TelegramUser.referral_code == referral_code))
        ref_user = ref_result.scalar_one_or_none()
        if not ref_user:
            return False
        referrer_id = ref_user.id
    if not referrer_id:
        return False
    session.add(
        ReferralEvent(
            referrer_id=referrer_id,
            referred_id=referred_id,
            referral_code=(referral_code or "")[:32] or None,
            event_type=(event_type or "unknown")[:32],
            source=(source or "unknown")[:32],
            meta=(meta or "")[:1000] or None,
        )
    )
    await session.commit()
    return True


async def list_referral_events(session: AsyncSession, referrer_id: int, limit: int = 200) -> list[dict[str, Any]]:
    referred = aliased(TelegramUser)
    rows = await session.execute(
        select(
            ReferralEvent,
            referred.telegram_id,
            referred.username,
            referred.full_name,
        )
        .join(referred, referred.id == ReferralEvent.referred_id, isouter=True)
        .where(ReferralEvent.referrer_id == referrer_id)
        .order_by(ReferralEvent.created_at.desc())
        .limit(limit)
    )
    out: list[dict[str, Any]] = []
    for event, tg_id, username, full_name in rows.all():
        out.append(
            {
                "id": event.id,
                "event_type": event.event_type,
                "source": event.source,
                "created_at": event.created_at,
                "referral_code": event.referral_code or "",
                "referred_id": event.referred_id,
                "referred_tg": int(tg_id) if tg_id is not None else None,
                "referred_name": (username or full_name or "").strip(),
                "meta": event.meta or "",
            }
        )
    return out


async def list_referral_clicks(session: AsyncSession, referrer_id: int, limit: int = 200) -> list[ReferralClick]:
    rows = await session.execute(
        select(ReferralClick)
        .where(ReferralClick.referrer_id == referrer_id)
        .order_by(ReferralClick.created_at.desc())
        .limit(limit)
    )
    return list(rows.scalars().all())


async def referral_referred_users(session: AsyncSession, referrer_id: int, limit: int = 200) -> list[dict[str, Any]]:
    rows = await session.execute(
        select(
            TelegramUser.id,
            TelegramUser.telegram_id,
            TelegramUser.username,
            TelegramUser.full_name,
            TelegramUser.created_at,
            TelegramUser.trial_used,
            func.count(PaymentLog.id).label("purchases"),
            func.coalesce(func.sum(PaymentLog.amount_usd), 0).label("revenue"),
        )
        .select_from(TelegramUser)
        .join(
            PaymentLog,
            and_(
                PaymentLog.user_id == TelegramUser.id,
                PaymentLog.status == PaymentStatus.CONFIRMED.value,
                _non_promo_clause(),
                _non_balance_clause(),
            ),
            isouter=True,
        )
        .where(TelegramUser.referred_by_id == referrer_id)
        .group_by(TelegramUser.id)
        .order_by(TelegramUser.created_at.desc())
        .limit(limit)
    )
    return [
        {
            "id": int(row[0]),
            "telegram_id": int(row[1]),
            "username": row[2] or "",
            "full_name": row[3] or "",
            "created_at": row[4],
            "trial_used": bool(row[5]),
            "purchases": int(row[6] or 0),
            "revenue_rub": float(row[7] or 0),
        }
        for row in rows.all()
    ]


async def referral_stats_for_user(session: AsyncSession, referrer_id: int) -> dict[str, int | float]:
    clicks = (
        await session.execute(select(func.count(ReferralClick.id)).where(ReferralClick.referrer_id == referrer_id))
    ).scalar_one()
    signups = (
        await session.execute(select(func.count(TelegramUser.id)).where(TelegramUser.referred_by_id == referrer_id))
    ).scalar_one()
    trials = (
        await session.execute(
            select(func.count(TelegramUser.id)).where(
                TelegramUser.referred_by_id == referrer_id,
                TelegramUser.trial_used.is_(True),
            )
        )
    ).scalar_one()
    payments = (
        await session.execute(
            select(func.count(PaymentLog.id))
            .join(TelegramUser, TelegramUser.id == PaymentLog.user_id)
        .where(
            TelegramUser.referred_by_id == referrer_id,
            PaymentLog.status == PaymentStatus.CONFIRMED.value,
            _non_promo_clause(),
            _non_balance_clause(),
        )
        )
    ).scalar_one()
    revenue = (
        await session.execute(
            select(func.coalesce(func.sum(PaymentLog.amount_usd), 0))
            .join(TelegramUser, TelegramUser.id == PaymentLog.user_id)
        .where(
            TelegramUser.referred_by_id == referrer_id,
            PaymentLog.status == PaymentStatus.CONFIRMED.value,
            _non_promo_clause(),
            _non_balance_clause(),
        )
        )
    ).scalar_one()
    return {
        "clicks": int(clicks or 0),
        "signups": int(signups or 0),
        "trials": int(trials or 0),
        "purchases": int(payments or 0),
        "revenue_rub": float(revenue or 0),
    }


async def referral_stats_for_user_v2(session: AsyncSession, referrer_id: int) -> dict[str, int | float]:
    invited = (
        await session.execute(select(func.count(TelegramUser.id)).where(TelegramUser.referred_by_id == referrer_id))
    ).scalar_one()
    earned = (
        await session.execute(
            select(func.coalesce(func.sum(BalanceLog.amount_usd), 0)).where(
                BalanceLog.user_id == referrer_id,
                BalanceLog.reason.ilike("referral_bonus_referrer_%"),
            )
        )
    ).scalar_one()
    active = (
        await session.execute(
            select(func.count(Referral.id)).where(
                Referral.referrer_id == referrer_id,
                Referral.reward_given.is_(True),
            )
        )
    ).scalar_one()
    return {
        "invited": int(invited or 0),
        "earned_bonus_rub": float(earned or 0),
        "active_referrals": int(active or 0),
    }


async def list_referrals_for_user(session: AsyncSession, referrer_id: int, limit: int = 100) -> list[dict[str, Any]]:
    referred = aliased(TelegramUser)
    rows = await session.execute(
        select(
            referred.telegram_id,
            referred.username,
            referred.full_name,
            referred.created_at,
            Referral.status,
            Referral.reward_given,
        )
        .select_from(referred)
        .join(Referral, Referral.referred_id == referred.id, isouter=True)
        .where(referred.referred_by_id == referrer_id)
        .order_by(referred.created_at.desc())
        .limit(limit)
    )
    out: list[dict[str, Any]] = []
    for tg_id, username, full_name, created_at, status, reward_given in rows.all():
        out.append(
            {
                "telegram_id": int(tg_id) if tg_id is not None else None,
                "username": username or "",
                "full_name": full_name or "",
                "created_at": created_at,
                "status": status or ("approved" if reward_given else "pending"),
                "reward_given": bool(reward_given),
            }
        )
    return out


async def referral_admin_stats(session: AsyncSession, limit: int = 200) -> list[dict[str, int | str | float]]:
    click_sub = (
        select(ReferralClick.referrer_id.label("rid"), func.count(ReferralClick.id).label("clicks"))
        .group_by(ReferralClick.referrer_id)
        .subquery()
    )
    start_sub = (
        select(ReferralEvent.referrer_id.label("rid"), func.count(ReferralEvent.id).label("starts"))
        .where(ReferralEvent.event_type == "start")
        .group_by(ReferralEvent.referrer_id)
        .subquery()
    )
    signup_sub = (
        select(TelegramUser.referred_by_id.label("rid"), func.count(TelegramUser.id).label("signups"))
        .where(TelegramUser.referred_by_id.is_not(None))
        .group_by(TelegramUser.referred_by_id)
        .subquery()
    )
    trial_sub = (
        select(TelegramUser.referred_by_id.label("rid"), func.count(TelegramUser.id).label("trials"))
        .where(TelegramUser.referred_by_id.is_not(None), TelegramUser.trial_used.is_(True))
        .group_by(TelegramUser.referred_by_id)
        .subquery()
    )
    pay_sub = (
        select(
            TelegramUser.referred_by_id.label("rid"),
            func.count(PaymentLog.id).label("purchases"),
            func.coalesce(func.sum(PaymentLog.amount_usd), 0).label("revenue"),
        )
        .select_from(PaymentLog)
        .join(TelegramUser, TelegramUser.id == PaymentLog.user_id)
        .where(
            PaymentLog.status == PaymentStatus.CONFIRMED.value,
            TelegramUser.referred_by_id.is_not(None),
            _non_promo_clause(),
            _non_balance_clause(),
        )
        .group_by(TelegramUser.referred_by_id)
        .subquery()
    )

    rows = await session.execute(
        select(
            TelegramUser.id,
            TelegramUser.telegram_id,
            TelegramUser.username,
            TelegramUser.referral_code,
            func.coalesce(click_sub.c.clicks, 0),
            func.coalesce(start_sub.c.starts, 0),
            func.coalesce(signup_sub.c.signups, 0),
            func.coalesce(trial_sub.c.trials, 0),
            func.coalesce(pay_sub.c.purchases, 0),
            func.coalesce(pay_sub.c.revenue, 0),
        )
        .select_from(TelegramUser)
        .join(click_sub, click_sub.c.rid == TelegramUser.id, isouter=True)
        .join(start_sub, start_sub.c.rid == TelegramUser.id, isouter=True)
        .join(signup_sub, signup_sub.c.rid == TelegramUser.id, isouter=True)
        .join(trial_sub, trial_sub.c.rid == TelegramUser.id, isouter=True)
        .join(pay_sub, pay_sub.c.rid == TelegramUser.id, isouter=True)
        .where(
            or_(
                click_sub.c.clicks.is_not(None),
                start_sub.c.starts.is_not(None),
                signup_sub.c.signups.is_not(None),
                trial_sub.c.trials.is_not(None),
                pay_sub.c.purchases.is_not(None),
            )
        )
        .order_by(func.coalesce(pay_sub.c.revenue, 0).desc(), func.coalesce(signup_sub.c.signups, 0).desc())
        .limit(limit)
    )
    return [
        {
            "id": int(row[0]),
            "telegram_id": int(row[1]),
            "username": row[2] or "",
            "referral_code": row[3] or "",
            "clicks": int(row[4] or 0),
            "starts": int(row[5] or 0),
            "signups": int(row[6] or 0),
            "trials": int(row[7] or 0),
            "purchases": int(row[8] or 0),
            "revenue_rub": float(row[9] or 0),
        }
        for row in rows.all()
    ]


async def create_promo(
    session: AsyncSession,
    code: str,
    discount_percent: int,
    bonus_days: int,
    bonus_balance_rub: Decimal,
    max_uses: int,
    expires_at: datetime | None,
) -> PromoCode:
    promo = PromoCode(
        code=code.upper().strip(),
        discount_percent=max(0, min(discount_percent, 100)),
        bonus_days=max(0, bonus_days),
        bonus_balance_rub=max(Decimal("0.00"), Decimal(bonus_balance_rub)).quantize(Decimal("0.01")),
        max_uses=max(1, max_uses),
        expires_at=expires_at,
        active=True,
    )
    session.add(promo)
    await session.commit()
    await session.refresh(promo)
    return promo


async def list_promos(session: AsyncSession, limit: int = 300) -> list[PromoCode]:
    result = await session.execute(select(PromoCode).order_by(PromoCode.id.desc()).limit(limit))
    return list(result.scalars().all())


async def set_promo_active(session: AsyncSession, promo_id: int, active: bool) -> None:
    await session.execute(update(PromoCode).where(PromoCode.id == promo_id).values(active=active))
    await session.commit()


async def redeem_promo_for_user(
    session: AsyncSession,
    user: TelegramUser,
    code: str,
) -> tuple[PromoCode | None, Decimal, bool]:
    """
    Redeems promo once and credits bonus balance immediately.
    Returns: (promo_or_none, credited_amount, redeemed_flag)
    """
    used = await apply_promo(session, code)
    if not used:
        return None, Decimal("0.00"), False

    credited = Decimal("0.00")
    bonus = Decimal(used.bonus_balance_rub or 0).quantize(Decimal("0.01"))
    if bonus > 0:
        await change_user_balance(session=session, user=user, amount_usd=bonus, reason=f"promo_bonus_{used.code}")
        credited = bonus
    return used, credited, True


async def change_user_balance(
    session: AsyncSession,
    user: TelegramUser,
    amount_usd: Decimal,
    reason: str,
    admin_telegram_id: int | None = None,
) -> TelegramUser:
    user.balance_usd = Decimal(user.balance_usd or 0) + Decimal(amount_usd)
    session.add(
        BalanceLog(
            user_id=user.id,
            amount_usd=Decimal(amount_usd),
            reason=reason[:64],
            admin_telegram_id=admin_telegram_id,
        )
    )
    await session.commit()
    await session.refresh(user)
    return user


async def debit_user_balance_if_enough(
    session: AsyncSession,
    user: TelegramUser,
    amount_usd: Decimal,
    reason: str,
    admin_telegram_id: int | None = None,
) -> bool:
    amount = Decimal(amount_usd)
    if amount <= 0:
        return False
    upd = (
        update(TelegramUser)
        .where(
            TelegramUser.id == user.id,
            TelegramUser.balance_usd >= amount,
        )
        .values(balance_usd=TelegramUser.balance_usd - amount)
    )
    res = await session.execute(upd)
    if not res.rowcount:
        await session.rollback()
        return False
    session.add(
        BalanceLog(
            user_id=user.id,
            amount_usd=-amount,
            reason=reason[:64],
            admin_telegram_id=admin_telegram_id,
        )
    )
    await session.commit()
    await session.refresh(user)
    return True


async def list_balance_logs(session: AsyncSession, user_id: int, limit: int = 50) -> list[BalanceLog]:
    result = await session.execute(
        select(BalanceLog).where(BalanceLog.user_id == user_id).order_by(BalanceLog.created_at.desc()).limit(limit)
    )
    return list(result.scalars().all())


async def analytics_summary(session: AsyncSession) -> dict[str, int | float]:
    total_users = (await session.execute(select(func.count(TelegramUser.id)))).scalar_one()
    active_subs = (
        await session.execute(select(func.count(TelegramUser.id)).where(TelegramUser.status == UserStatus.ACTIVE.value))
    ).scalar_one()
    revenue = (
        await session.execute(
            select(func.coalesce(func.sum(PaymentLog.amount_usd), 0)).where(
                PaymentLog.status == PaymentStatus.CONFIRMED.value,
                _non_promo_clause(),
                _non_balance_clause(),
            )
        )
    ).scalar_one()
    promo_revenue = (
        await session.execute(
            select(func.coalesce(func.sum(PaymentLog.amount_usd), 0)).where(
                PaymentLog.status == PaymentStatus.CONFIRMED.value,
                _promo_used_clause(),
                _non_balance_clause(),
            )
        )
    ).scalar_one()
    pending = (
        await session.execute(select(func.count(PaymentLog.id)).where(PaymentLog.status == PaymentStatus.PENDING.value))
    ).scalar_one()
    total_balance = (await session.execute(select(func.coalesce(func.sum(TelegramUser.balance_usd), 0)))).scalar_one()
    return {
        "total_users": int(total_users or 0),
        "active_subscriptions": int(active_subs or 0),
        "revenue_rub": float(revenue or 0),
        "pending_payments": int(pending or 0),
        "total_balance_rub": float(total_balance or 0),
        "promo_revenue_rub": float(promo_revenue or 0),
        # backward-compatible aliases
        "revenue_usd": float(revenue or 0),
        "promo_revenue_usd": float(promo_revenue or 0),
        "total_balance_usd": float(total_balance or 0),
    }


async def sales_timeseries(session: AsyncSession, days: int = 30) -> list[dict[str, str | float]]:
    start = datetime.utcnow() - timedelta(days=days)
    rows = await session.execute(
        select(func.date(PaymentLog.confirmed_at), func.coalesce(func.sum(PaymentLog.amount_usd), 0))
        .where(
            PaymentLog.status == PaymentStatus.CONFIRMED.value,
            PaymentLog.confirmed_at.is_not(None),
            PaymentLog.confirmed_at >= start,
            _non_promo_clause(),
            _non_balance_clause(),
        )
        .group_by(func.date(PaymentLog.confirmed_at))
        .order_by(func.date(PaymentLog.confirmed_at))
    )
    return [{"day": str(day), "revenue": float(total)} for day, total in rows.all()]


async def top_referrers(session: AsyncSession, limit: int = 10) -> list[dict[str, int | str]]:
    referred = aliased(TelegramUser)
    result = await session.execute(
        select(TelegramUser.id, TelegramUser.telegram_id, TelegramUser.username, func.count(referred.id))
        .select_from(TelegramUser)
        .join(referred, referred.referred_by_id == TelegramUser.id, isouter=True)
        .group_by(TelegramUser.id, TelegramUser.telegram_id, TelegramUser.username)
        .order_by(func.count(referred.id).desc())
        .limit(limit)
    )
    return [
        {"id": row[0], "telegram_id": row[1], "username": row[2] or "", "referrals": int(row[3] or 0)}
        for row in result.all()
    ]


async def finance_summary(session: AsyncSession, days: int = 30) -> dict[str, int | float]:
    start = datetime.utcnow() - timedelta(days=days)
    confirmed_count = (
        await session.execute(
            select(func.count(PaymentLog.id)).where(
                PaymentLog.status == PaymentStatus.CONFIRMED.value,
                PaymentLog.confirmed_at.is_not(None),
                PaymentLog.confirmed_at >= start,
            )
        )
    ).scalar_one()
    confirmed_revenue = (
        await session.execute(
            select(func.coalesce(func.sum(PaymentLog.amount_usd), 0)).where(
                PaymentLog.status == PaymentStatus.CONFIRMED.value,
                PaymentLog.confirmed_at.is_not(None),
                PaymentLog.confirmed_at >= start,
                _non_promo_clause(),
                _non_balance_clause(),
            )
        )
    ).scalar_one()
    promo_revenue = (
        await session.execute(
            select(func.coalesce(func.sum(PaymentLog.amount_usd), 0)).where(
                PaymentLog.status == PaymentStatus.CONFIRMED.value,
                PaymentLog.confirmed_at.is_not(None),
                PaymentLog.confirmed_at >= start,
                _promo_used_clause(),
                _non_balance_clause(),
            )
        )
    ).scalar_one()
    pending_count = (
        await session.execute(select(func.count(PaymentLog.id)).where(PaymentLog.status == PaymentStatus.PENDING.value))
    ).scalar_one()
    avg_check = 0
    if confirmed_count:
        avg_check = float(confirmed_revenue or 0) / int(confirmed_count or 1)
    promo_bonus_sum = (
        await session.execute(
            select(func.coalesce(func.sum(BalanceLog.amount_usd), 0)).where(BalanceLog.reason.ilike("promo_bonus_%"))
        )
    ).scalar_one()
    return {
        "window_days": days,
        "orders_confirmed": int(confirmed_count or 0),
        "revenue_rub": float(confirmed_revenue or 0),
        "pending_orders": int(pending_count or 0),
        "avg_check_rub": round(avg_check, 2),
        "promo_bonus_paid_rub": float(promo_bonus_sum or 0),
        "promo_revenue_rub": float(promo_revenue or 0),
    }


async def payment_method_breakdown(session: AsyncSession, days: int = 30) -> list[dict[str, str | int | float]]:
    start = datetime.utcnow() - timedelta(days=days)
    rows = await session.execute(
        select(
            PaymentLog.method,
            func.count(PaymentLog.id),
            func.coalesce(func.sum(PaymentLog.amount_usd), 0),
        )
        .where(
            PaymentLog.status == PaymentStatus.CONFIRMED.value,
            PaymentLog.confirmed_at.is_not(None),
            PaymentLog.confirmed_at >= start,
            _non_promo_clause(),
            _non_balance_clause(),
        )
        .group_by(PaymentLog.method)
        .order_by(func.coalesce(func.sum(PaymentLog.amount_usd), 0).desc())
    )
    return [
        {"method": method or "unknown", "orders": int(orders or 0), "revenue": float(revenue or 0)}
        for method, orders, revenue in rows.all()
    ]


async def users_growth_timeseries(session: AsyncSession, days: int = 30) -> list[dict[str, Any]]:
    start = datetime.utcnow() - timedelta(days=days)
    rows = await session.execute(
        select(
            func.date(TelegramUser.created_at),
            func.count(TelegramUser.id),
            func.sum(case((TelegramUser.referred_by_id.is_not(None), 1), else_=0)),
        )
        .where(TelegramUser.created_at >= start)
        .group_by(func.date(TelegramUser.created_at))
        .order_by(func.date(TelegramUser.created_at))
    )
    out: list[dict[str, Any]] = []
    for day, total, referred in rows.all():
        total_count = int(total or 0)
        ref_count = int(referred or 0)
        out.append(
            {
                "day": str(day),
                "total": total_count,
                "referral": ref_count,
                "organic": max(0, total_count - ref_count),
            }
        )
    return out


def _desktop_code() -> str:
    # 8 chars, easy to type, avoids ambiguous symbols.
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    return "".join(secrets.choice(alphabet) for _ in range(8))


async def create_desktop_login_code(
    session: AsyncSession,
    telegram_id: int,
    ttl_seconds: int = 300,
) -> DesktopLoginCode:
    now = datetime.utcnow()
    exp = now + timedelta(seconds=max(30, int(ttl_seconds)))
    # Best-effort cleanup of expired unused codes for this user.
    await session.execute(
        delete(DesktopLoginCode).where(
            DesktopLoginCode.telegram_id == telegram_id,
            DesktopLoginCode.used_at.is_(None),
            DesktopLoginCode.expires_at < now,
        )
    )
    # Generate a unique code.
    for _ in range(10):
        code = _desktop_code()
        existing = await session.execute(select(DesktopLoginCode).where(DesktopLoginCode.code == code))
        if existing.scalar_one_or_none():
            continue
        row = DesktopLoginCode(
            telegram_id=telegram_id,
            code=code,
            created_at=now,
            expires_at=exp,
            used_at=None,
        )
        session.add(row)
        await session.commit()
        await session.refresh(row)
        return row
    raise RuntimeError("Failed to generate unique desktop login code")


async def consume_desktop_login_code(session: AsyncSession, code: str) -> int | None:
    now = datetime.utcnow()
    code = (code or "").strip().upper()
    if not code:
        return None
    result = await session.execute(
        select(DesktopLoginCode).where(
            DesktopLoginCode.code == code,
            DesktopLoginCode.used_at.is_(None),
            DesktopLoginCode.expires_at >= now,
        )
    )
    row = result.scalar_one_or_none()
    if not row:
        return None
    row.used_at = now
    await session.commit()
    return int(row.telegram_id)


async def get_desktop_trial_device(session: AsyncSession, device_id: str) -> DesktopTrialDevice | None:
    result = await session.execute(select(DesktopTrialDevice).where(DesktopTrialDevice.device_id == device_id))
    return result.scalar_one_or_none()


async def upsert_desktop_trial_device(
    session: AsyncSession,
    device_id: str,
    user_id: int | None,
    marzban_username: str | None = None,
    last_granted_at: datetime | None = None,
    last_ip: str | None = None,
) -> DesktopTrialDevice:
    row = await get_desktop_trial_device(session, device_id)
    if row:
        if user_id is not None:
            row.user_id = user_id
        if marzban_username is not None:
            row.marzban_username = marzban_username
        if last_granted_at is not None:
            row.last_granted_at = last_granted_at
        if last_ip is not None:
            row.last_ip = last_ip
        await session.commit()
        await session.refresh(row)
        return row

    row = DesktopTrialDevice(
        device_id=device_id,
        user_id=user_id,
        marzban_username=marzban_username,
        last_granted_at=last_granted_at,
        last_ip=last_ip,
        created_at=datetime.utcnow(),
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row


async def top_plans_by_revenue(session: AsyncSession, days: int = 30, limit: int = 10) -> list[dict[str, str | int | float]]:
    start = datetime.utcnow() - timedelta(days=days)
    rows = await session.execute(
        select(
            Plan.name,
            func.count(PaymentLog.id),
            func.coalesce(func.sum(PaymentLog.amount_usd), 0),
        )
        .select_from(PaymentLog)
        .join(Plan, Plan.id == PaymentLog.plan_id, isouter=True)
        .where(
            PaymentLog.status == PaymentStatus.CONFIRMED.value,
            PaymentLog.confirmed_at.is_not(None),
            PaymentLog.confirmed_at >= start,
            _non_promo_clause(),
            _non_balance_clause(),
        )
        .group_by(Plan.name)
        .order_by(func.coalesce(func.sum(PaymentLog.amount_usd), 0).desc())
        .limit(limit)
    )
    return [
        {"name": name or "No plan", "orders": int(orders or 0), "revenue": float(revenue or 0)}
        for name, orders, revenue in rows.all()
    ]
