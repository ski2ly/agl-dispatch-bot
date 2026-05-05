import os
import logging
import json
import time
from datetime import datetime
from aiohttp import web
import aiohttp_cors
from database import db
from utils.security import verify_init_data, extract_user_from_init_data
from utils.helpers import build_card, build_bid_card, sync_bid_to_discussion
import hashlib

logger = logging.getLogger(__name__)
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_ID = os.getenv("CHANNEL_ID")
_upload_counts = {}
_upload_counts_last_gc = 0.0
_bid_cooldowns = {} # (user_id, req_id) -> (hash, timestamp)


def _gc_upload_counts(now: float):
    """Drop entries we haven't seen in over a minute. Prevents unbounded memory growth."""
    global _upload_counts_last_gc
    if now - _upload_counts_last_gc < 60:
        return
    _upload_counts_last_gc = now
    stale = [uid for uid, times in _upload_counts.items() if not times or now - times[-1] > 300]
    for uid in stale:
        _upload_counts.pop(uid, None)

def json_serial(obj):
    """JSON serializer for objects not serializable by default json code"""
    if isinstance(obj, datetime):
        return obj.isoformat()
    raise TypeError ("Type %s not serializable" % type(obj))

def safe_json_response(data):
    return web.json_response(data, dumps=lambda x: json.dumps(x, default=json_serial))

async def check_auth(request):
    """Helper to verify initData and ensure user exists in DB."""
    try:
        if request.method == 'GET':
            init_data = request.query.get("initData", "")
        else:
            body = await request.json()
            init_data = body.get("initData", "")
    except Exception as e:
        logger.error(f"Failed to get initData from request: {e}")
        return None, "Invalid request format"

    if not verify_init_data(init_data, BOT_TOKEN):
        logger.warning(f"Auth failed: Invalid initData or token (Token len: {len(BOT_TOKEN) if BOT_TOKEN else 0})")
        return None, "Unauthorized"
    
    user_id, user_name = extract_user_from_init_data(init_data)
    if not user_id:
        logger.warning(f"Auth failed: Could not extract user_id from initData")
        return None, "Invalid user data"

    profile = await db.get_user(user_id)
    if not profile:
        logger.warning(f"Auth failed: Profile not found for ID {user_id} ({user_name}). Fallback check should have handled this if they are a superuser.")
        return None, "Profile not found"
    
    logger.info(f"Auth success: {user_name} ({user_id}) as {profile.get('role')}")
    return profile, None

async def api_health(request):
    return web.json_response({"status": "ok"})

async def api_profile(request):
    """Get the profile of the current authenticated user."""
    profile, err = await check_auth(request)
    if err: return safe_json_response({"error": err, "auth_needed": True})
    return safe_json_response({"profile": profile})

async def api_users(request):
    """Admin only: list all users."""
    profile, err = await check_auth(request)
    if err or profile["role"] not in ["admin", "superuser"]:
        return safe_json_response({"error": "Forbidden", "auth_needed": True})
    
    try:
        users = await db.list_users()
        is_superuser = profile.get('role') == 'superuser'
        
        # Superuser sees everything, including login keys. 
        # Regular admins see the list but NOT the keys.
        clean_users = []
        for u in users:
            if u.get('role') == 'superuser':
                continue
                
            user_data = dict(u)
            if not is_superuser:
                user_data.pop('login_key', None)
            clean_users.append(user_data)
        
        return safe_json_response({"ok": True, "users": clean_users})
    except Exception as e:
        logger.error(f"api_users error: {e}")
        return safe_json_response({"error": str(e), "users": []})

ALLOWED_ROLES = {"manager", "admin"}  # superuser is granted only via SUPERUSER_IDS env


def _validate_name(name: str) -> str | None:
    if not name or not isinstance(name, str):
        return None
    name = name.strip()
    if not (1 <= len(name) <= 64):
        return None
    return name


async def api_user_update(request):
    """Update user name/role or delete a user. Identifies the target by id (preferred)."""
    data = await request.json()
    init_data = data.get("initData", "")
    if not verify_init_data(init_data, BOT_TOKEN):
        return safe_json_response({"error": "Unauthorized"})

    admin_id, _ = extract_user_from_init_data(init_data)
    admin_profile = await db.get_user(admin_id)
    if not admin_profile or admin_profile["role"] not in ["admin", "superuser"]:
        return safe_json_response({"error": "Forbidden"})

    target_user_id = data.get("user_id") or data.get("id")
    action = data.get("action")
    new_role = data.get("role")
    new_name = data.get("name")

    if not target_user_id:
        return safe_json_response({"error": "user_id required"})
    try:
        target_user_id = int(target_user_id)
    except (ValueError, TypeError):
        return safe_json_response({"error": "user_id must be int"})

    # Safety: do not allow modifying superusers via this endpoint, and don't let an admin
    # delete or demote their own account (would lock them out instantly).
    async with db._pool.acquire() as conn:
        target_user = await conn.fetchrow("SELECT id, telegram_id, role FROM users WHERE id = $1", target_user_id)
    if not target_user:
        return safe_json_response({"error": "User not found"})
    if target_user["role"] == "superuser":
        return safe_json_response({"error": "Cannot modify superuser via API"})
    # Self-delete / self-demote guard
    if target_user["telegram_id"] and int(target_user["telegram_id"]) == int(admin_id):
        if action == "delete":
            return safe_json_response({"error": "Нельзя удалить собственный аккаунт"})
        if new_role and new_role != admin_profile["role"]:
            return safe_json_response({"error": "Нельзя сменить собственную роль"})

    try:
        if action == "delete":
            await db.delete_user_by_id(target_user_id)
            return safe_json_response({"ok": True})

        # Update path
        validated_name = _validate_name(new_name) if new_name is not None else None
        if new_name is not None and validated_name is None:
            return safe_json_response({"error": "Invalid name"})
        if new_role is not None and new_role not in ALLOWED_ROLES:
            return safe_json_response({"error": f"Role must be one of: {sorted(ALLOWED_ROLES)}"})

        await db.update_user_profile(target_user_id, name=validated_name, role=new_role)
        return safe_json_response({"ok": True})
    except Exception as e:
        logger.error(f"User update error: {e}")
        return safe_json_response({"error": "Internal error"})


async def api_user_create(request):
    """Admin only: pre-create a user record. Server generates a login key and returns it once."""
    data = await request.json()
    init_data = data.get("initData", "")
    if not verify_init_data(init_data, BOT_TOKEN):
        return safe_json_response({"error": "Unauthorized"})

    admin_id, _ = extract_user_from_init_data(init_data)
    admin_profile = await db.get_user(admin_id)
    if not admin_profile or admin_profile["role"] not in ["admin", "superuser"]:
        return safe_json_response({"error": "Forbidden"})

    name = _validate_name(data.get("name"))
    role = data.get("role")
    manual_key = data.get("login_key")
    
    if not name:
        return safe_json_response({"error": "Invalid name"})
    if role not in ALLOWED_ROLES:
        return safe_json_response({"error": f"Role must be one of: {sorted(ALLOWED_ROLES)}"})

    from database import generate_login_key
    new_key = manual_key.strip() if manual_key and manual_key.strip() else generate_login_key()
    
    try:
        await db.create_user(name, role, new_key)
        # Plaintext returned exactly once — never stored in DB or logs.
        return safe_json_response({"ok": True, "login_key": new_key})
    except Exception as e:
        logger.error(f"User create error: {e}")
        return safe_json_response({"error": "Internal error"})

# Brute-force protection on /api/login_with_key.
# We track failed attempts per Telegram ID; after MAX_FAILS within WINDOW_S seconds
# the user must wait BAN_S before trying again. This caps any single attacker to
# a few attempts per minute regardless of HTTP throughput.
_login_fails: dict[int, list[float]] = {}
_LOGIN_MAX_FAILS = 5
_LOGIN_WINDOW_S = 60
_LOGIN_BAN_S = 300


def _check_login_rate(tg_id: int, now: float) -> str | None:
    fails = [t for t in _login_fails.get(tg_id, []) if now - t < _LOGIN_BAN_S]
    _login_fails[tg_id] = fails
    recent = [t for t in fails if now - t < _LOGIN_WINDOW_S]
    if len(recent) >= _LOGIN_MAX_FAILS:
        return f"Слишком много попыток. Подождите {_LOGIN_BAN_S // 60} минут."
    return None


def _record_login_fail(tg_id: int, now: float):
    _login_fails.setdefault(tg_id, []).append(now)
    # Bound dict size — drop entries older than the ban window.
    if len(_login_fails) > 5000:
        for k in list(_login_fails.keys()):
            _login_fails[k] = [t for t in _login_fails[k] if now - t < _LOGIN_BAN_S]
            if not _login_fails[k]:
                _login_fails.pop(k, None)


async def api_login_with_key(request):
    """Link telegram_id to user profile using login_key (hashed lookup, atomic)."""
    data = await request.json()
    init_data = data.get("initData", "")
    if not verify_init_data(init_data, BOT_TOKEN):
        return safe_json_response({"error": "Unauthorized"})

    tg_id, _ = extract_user_from_init_data(init_data)
    if not tg_id:
        return safe_json_response({"error": "Invalid Telegram identity"})

    now = time.time()
    rate_err = _check_login_rate(tg_id, now)
    if rate_err:
        return safe_json_response({"error": rate_err})

    login_key = (data.get("login_key") or "").strip()
    if not login_key or len(login_key) > 64:
        _record_login_fail(tg_id, now)
        return safe_json_response({"error": "Missing or invalid key"})

    try:
        user = await db.link_telegram_to_key(login_key, tg_id)
        if not user:
            _record_login_fail(tg_id, now)
            logger.warning(f"Login attempt with invalid key from tg_id={tg_id}")
            return safe_json_response({"error": "Invalid key"})
        # Success — clear the fail counter.
        _login_fails.pop(tg_id, None)
        return safe_json_response({"ok": True, "name": user['name']})
    except Exception as e:
        logger.error(f"api_login_with_key error: {e}")
        return safe_json_response({"error": "Internal error"})

async def api_requests(request):
    profile, err = await check_auth(request)
    if err: return safe_json_response({"error": err, "auth_needed": True})
    
    status = request.query.get("status")
    region = request.query.get("region")
    manager = request.query.get("manager")
    search = request.query.get("search")
    transport = request.query.get("transport")
    
    reqs = await db.list_requests(status=status, region=region, manager=manager, search=search, transport=transport)
    return safe_json_response({"requests": reqs})

async def api_request_details(request):
    """Get full details for a specific request."""
    profile, err = await check_auth(request)
    if err: return safe_json_response({"error": err, "auth_needed": True})

    req_id_raw = request.query.get("id")
    try:
        req_id = int(req_id_raw)
    except (TypeError, ValueError):
        return safe_json_response({"error": "Invalid id"})

    req = await db.get_request(req_id)
    if not req:
        return safe_json_response({"error": "Not found"})
    bids = await db.get_bids(req_id)
    comments = await db.get_comments(req_id)
    attachments = await db.get_attachments(req_id)
    return safe_json_response({"request": req, "bids": bids, "comments": comments, "attachments": attachments})

async def api_my_bids(request):
    """List bids for the current user."""
    profile, err = await check_auth(request)
    if err: return safe_json_response({"error": err, "auth_needed": True})
    
    user_id = profile['telegram_id']
    if not user_id:
        return safe_json_response({"error": "Invalid user identity", "bids": []})
        
    bids = await db.get_user_bids(user_id)
    return safe_json_response({"bids": bids})

async def api_my_requests(request):
    """List requests created by the current user."""
    profile, err = await check_auth(request)
    if err: return safe_json_response({"error": err, "auth_needed": True})
    
    user_id = profile['telegram_id']
    reqs = await db.list_requests(creator_id=user_id)
    return safe_json_response({"requests": reqs})

async def api_bid_cancel(request):
    """Cancel own bid — employees only."""
    profile, err = await check_auth(request)
    if err:
        return safe_json_response({"error": err, "auth_needed": True})
    user_id = profile.get("telegram_id")
    if not user_id:
        return safe_json_response({"error": "Invalid identity"})

    data = await request.json()
    try:
        bid_id = int(data.get("id"))
    except (TypeError, ValueError):
        return safe_json_response({"error": "Invalid bid id"})

    async with db._pool.acquire() as conn:
        await conn.execute("UPDATE bids SET status = 'Отменена' WHERE id = $1 AND user_id = $2", bid_id, user_id)
        return safe_json_response({"ok": True})

async def api_update_status(request):
    """Update request status. Only the creator of the request or an admin can do it."""
    profile, err = await check_auth(request)
    if err:
        return safe_json_response({"error": err, "auth_needed": True})

    data = await request.json()
    try:
        req_id = int(data.get("id"))
    except (TypeError, ValueError):
        return safe_json_response({"error": "Invalid id"})
    new_status = data.get("status")
    if new_status not in {"Открыта", "В работе", "Успешно реализована", "Отменена"}:
        return safe_json_response({"error": "Invalid status"})

    # Authorization: only creator or admin/superuser may change status.
    existing = await db.get_request(req_id)
    if not existing:
        return safe_json_response({"error": "Not found"})
    is_admin = profile.get("role") in ("admin", "superuser")
    is_creator = existing.get("creator_id") and int(existing["creator_id"]) == int(profile["telegram_id"])
    if not (is_admin or is_creator):
        return safe_json_response({"error": "Только создатель заявки или администратор может менять статус"})

    winner = data.get("winner")
    reason = data.get("reason") or data.get("cancel_reason")
    if reason is not None:
        reason = str(reason).strip()[:500]

    try:
        async with db._pool.acquire() as conn:
            await conn.execute(
                "UPDATE requests SET status = $1, cancel_reason = $2, updated_at = NOW() WHERE id = $3",
                new_status, reason, req_id,
            )
            if new_status == 'Успешно реализована' and winner:
                await conn.execute("UPDATE bids SET status = 'Выиграла' WHERE request_id = $1 AND manager_name = $2", req_id, winner)
                await conn.execute("UPDATE bids SET status = 'Проиграла' WHERE request_id = $1 AND manager_name != $2", req_id, winner)
        
        # Sync to channel
        updated_req = await db.get_request(int(req_id))
        if updated_req and updated_req.get("channel_msg_id"):
            settings = await db.get_settings()
            target_channel = settings.get("channel_id") or os.getenv("CHANNEL_ID")
            if target_channel:
                try:
                    text = build_card(updated_req)
                    if updated_req['status'] != 'Открыта':
                        status_emoji = "✅" if "Успешно" in updated_req['status'] else "❌"
                        text = f"{status_emoji} СТАТУС: {updated_req['status'].upper()}\n\n" + text
                    await request.app["bot"].edit_message_text(
                        chat_id=target_channel,
                        message_id=int(updated_req["channel_msg_id"]),
                        text=text
                    )
                except Exception as e:
                    logger.error(f"Failed to update channel status: {e}")

        return safe_json_response({"ok": True})
    except Exception as e:
        logger.error(f"api_update_status error: {e}")
        return safe_json_response({"error": "Internal error"})

async def api_export_xlsx(request):
    """Admin only: Generate XLSX and send via Telegram Bot."""
    data = await request.json()
    init_data = data.get("initData", "")
    if not verify_init_data(init_data, BOT_TOKEN):
        return safe_json_response({"error": "Unauthorized"})
    
    admin_id, _ = extract_user_from_init_data(init_data)
    admin_profile = await db.get_user(admin_id)
    if not admin_profile or admin_profile["role"] not in ["admin", "superuser"]:
        return safe_json_response({"error": "Forbidden"})
    
    try:
        all_reqs = await db.get_requests_for_export()
        if not all_reqs:
            return safe_json_response({"error": "No data to export"})
            
        # Convert records to list of dicts and format data
        data_list = []
        for r in all_reqs:
            row = dict(r)
            # Format ID: 29 -> #00029
            if row.get('id'):
                row['id'] = f"#{int(row['id']):05d}"
            # Format datetimes
            if row.get('created_at'):
                row['created_at'] = row['created_at'].strftime('%d.%m.%Y %H:%M')
            if row.get('first_bid_at'):
                row['first_bid_at'] = row['first_bid_at'].strftime('%d.%m.%Y %H:%M')
            else:
                row['first_bid_at'] = "-"
            
            # Round response time
            if row.get('response_time_min') is not None:
                row['response_time_min'] = round(float(row['response_time_min']), 1)
            else:
                row['response_time_min'] = "-"
                
            data_list.append(row)
            
        # Define pretty mapping and order
        mapping = {
            'id': 'ID Заявки',
            'created_at': 'Дата создания',
            'first_bid_at': 'Первая ставка',
            'response_time_min': 'Время до 1-й ставки (мин)',
            'bids_count': 'Кол-во ставок',
            'responsible': 'Ответственный',
            'status': 'Статус',
            'regions': 'Регион',
            'route_from': 'Откуда',
            'route_to': 'Куда',
            'transport_cat': 'Тип транспорта',
            'transport_sub': 'Подтип',
            'cargo_name': 'Наименование груза',
            'cargo_value': 'Стоимость груза',
            'cargo_weight': 'Вес',
            'cargo_places': 'Места',
            'client_company': 'Компания клиента',
            'contact_phone': 'Телефон клиента',
            'cancel_reason': 'Причина отмены',
            'target': 'Таргет ($)',
            'message_text': 'Доп. информация'
        }
        
        try:
            import pandas as pd
            import io
            
            df = pd.DataFrame(data_list)
            
            # Filter and rename columns based on mapping
            existing_cols = [c for c in mapping.keys() if c in df.columns]
            df = df[existing_cols].rename(columns=mapping)
            
            output = io.BytesIO()
            with pd.ExcelWriter(output, engine='openpyxl') as writer:
                df.to_excel(writer, index=False, sheet_name='Аналитика Заявок')
            output.seek(0)
            
            bot = request.app["bot"]
            await bot.send_document(
                chat_id=admin_id,
                document=output,
                filename=f"AGL_Analytics_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx",
                caption="📊 Профессиональная выгрузка базы заявок для аналитики."
            )
            return safe_json_response({"ok": True})
            
        except Exception as excel_err:
            logger.warning(f"Excel generation failed, falling back to CSV: {excel_err}")
            # Fallback to CSV with Russian headers
            import csv
            import io
            
            output = io.StringIO()
            # Select columns for CSV
            csv_cols = [c for c in mapping.keys() if data_list and c in data_list[0]]
            
            writer = csv.DictWriter(output, fieldnames=csv_cols, extrasaction='ignore')
            # Write custom headers
            writer.writerow({c: mapping[c] for c in csv_cols})
            writer.writerows(data_list)
            
            output_bytes = io.BytesIO(output.getvalue().encode('utf-8-sig'))
            
            bot = request.app["bot"]
            await bot.send_document(
                chat_id=admin_id,
                document=output_bytes,
                filename=f"AGL_Export_{datetime.now().strftime('%Y%m%d_%H%M')}.csv",
                caption="⚠️ XLSX не удался, отправляю CSV с русскими заголовками."
            )
            return safe_json_response({"ok": True})

    except Exception as e:
        logger.error(f"Export error: {e}")
        return safe_json_response({"error": "Internal error"})



ALLOWED_REQUEST_FIELDS = {
    "responsible", "status", "regions", "transport_cat", "transport_sub",
    "cargo_name", "hs_code", "cargo_value", "cargo_weight", "cargo_places",
    "route_from", "route_to", "client_company", "contact_name", "contact_phone",
    "message_text", "target", "delivery_terms", "route_type", "loading_address",
    "customs_address", "clearance_address", "unloading_address", "transit_rf",
    "border_crossing", "urgency_type", "export_decl", "origin_cert",
    "container_type", "road_type", "container_owner", "glonass_seal",
    "seal_instructions", "flight_type", "stackable", "departure_ports",
    "multimodal_next", "company", "delivery_terms_eu", "transit_rf_allowed",
    "road_type_cn", "border_crossing_cn", "container_type_cn", "loading_days",
    "customs_days", "urgency_days", "ports_list", "dangerous_cargo", "packaging",
    "cancel_reason", "channel_msg_id", "mute_reminders", "last_notified_at", "winner_name",
}
ALLOWED_STATUSES = {"Открыта", "В работе", "Успешно реализована", "Отменена"}


def _sanitize_request_payload(payload: dict, *, is_admin: bool) -> dict:
    """Whitelist fields and trim values. Drops anything we don't recognise."""
    if not isinstance(payload, dict):
        return {}
    cleaned = {}
    for k, v in payload.items():
        if k not in ALLOWED_REQUEST_FIELDS:
            continue
        if isinstance(v, str):
            v = v.strip()[:4000]
        cleaned[k] = v
    # Status is privileged: only the canonical set, and "Успешно реализована" / "Отменена"
    # ideally go through api_update_status — but we still accept them on edit for the admin form.
    if "status" in cleaned and cleaned["status"] not in ALLOWED_STATUSES:
        cleaned.pop("status")
    return cleaned


async def api_submit(request):
    """Create or update a request."""
    profile, err = await check_auth(request)
    if err: return safe_json_response({"error": err, "auth_needed": True})

    data = await request.json()
    action = data.get("action", "new")
    req_id = data.get("id") or data.get("req_id")
    raw_payload = data.get("payload", {})
    payload = _sanitize_request_payload(raw_payload, is_admin=profile.get("role") in ("admin", "superuser"))

    try:
        if action == "edit" and req_id:
            try:
                req_id_int = int(req_id)
            except (TypeError, ValueError):
                return safe_json_response({"error": "Invalid id"})
            if not payload:
                return safe_json_response({"error": "Empty payload"})

            # Authorization: only the creator or an admin can edit a request.
            existing = await db.get_request(req_id_int)
            if not existing:
                return safe_json_response({"error": "Not found"})
            is_admin = profile.get("role") in ("admin", "superuser")
            is_creator = existing.get("creator_id") and int(existing["creator_id"]) == int(profile["telegram_id"])
            if not (is_admin or is_creator):
                return safe_json_response({"error": "Только создатель заявки или администратор может её редактировать"})

            await db.update_request(req_id_int, payload)
            final_id = req_id_int
            
            # Post UPDATE to channel if status or key fields changed
            updated_req = await db.get_request(final_id)
            if updated_req and updated_req.get("channel_msg_id"):
                settings = await db.get_settings()
                target_channel = settings.get("channel_id") or os.getenv("CHANNEL_ID")
                if target_channel:
                    try:
                        text = build_card(updated_req)
                        # If status is not 'Открыта', we can prepend it
                        if updated_req['status'] != 'Открыта':
                            status_emoji = "✅" if "Успешно" in updated_req['status'] else "❌"
                            text = f"{status_emoji} СТАТУС: {updated_req['status'].upper()}\n\n" + text
                        
                        await request.app["bot"].edit_message_text(
                            chat_id=target_channel,
                            message_id=int(updated_req["channel_msg_id"]),
                            text=text
                        )
                    except Exception as e:
                        logger.error(f"Failed to update channel message: {e}")
        else:
            # New request
            user_id = profile['telegram_id']
            payload["creator_id"] = user_id
            payload["creator_name"] = profile["name"]
            req = await db.create_request(payload)
            final_id = req["id"]
            
            # Link attachments if any
            attachment_ids = data.get("attachment_ids", [])
            if attachment_ids:
                await db.link_attachments(final_id, attachment_ids)
            
            await db.log_activity(final_id, user_id, profile["name"], "created_via_webapp")
            
            # Post to channel
            settings = await db.get_settings()
            target_channel = settings.get("channel_id") or os.getenv("CHANNEL_ID")
            
            if target_channel:
                msg = await request.app["bot"].send_message(chat_id=target_channel, text=build_card(req))
                await db.update_request(final_id, {"channel_msg_id": msg.message_id})
            
        return safe_json_response({"ok": True, "id": final_id})
    except Exception as e:
        logger.error(f"api_submit error: {e}")
        return safe_json_response({"error": "Internal error"})

ALLOWED_CURRENCIES = {"USD", "EUR", "RUB", "CNY", "UZS", "KZT"}


def _validate_bid(data):
    """Validate bid payload. Returns (req_id, err_str_or_None)."""
    raw_req = data.get("req_id") or data.get("request_id")
    try:
        req_id = int(raw_req)
    except (TypeError, ValueError):
        return None, "req_id required (int)"
    if req_id <= 0:
        return None, "req_id must be positive"

    amount_raw = data.get("amount")
    if amount_raw in (None, ""):
        return None, "amount required"
    # Accept strings like "1234" or "1234.56" or "1 234" — common manager inputs.
    s = str(amount_raw).replace(" ", "").replace(",", ".")
    try:
        amount_val = float(s)
    except ValueError:
        return None, "amount must be a number"
    if amount_val <= 0 or amount_val > 1e9:
        return None, "amount out of range"

    currency = (data.get("currency") or "").upper().strip()
    if currency and currency not in ALLOWED_CURRENCIES:
        return None, f"currency must be one of {sorted(ALLOWED_CURRENCIES)}"

    return req_id, None


async def api_bid(request):
    # Bids modify business data — must be a registered employee, not just any Telegram user.
    profile, err = await check_auth(request)
    if err:
        return web.json_response({"error": err, "auth_needed": True}, status=401)
    user_id = profile.get("telegram_id")
    if not user_id:
        return web.json_response({"error": "Invalid identity"}, status=401)

    data = await request.json()
    req_id, err = _validate_bid(data)
    if err:
        return web.json_response({"error": err}, status=400)

    try:
        # Deduplication: prevent same bid from same user on same request within 5 seconds
        bid_content = json.dumps(data, sort_keys=True)
        bid_hash = hashlib.md5(bid_content.encode()).hexdigest()
        now = time.time()
        cooldown_key = (user_id, int(req_id))
        
        if cooldown_key in _bid_cooldowns:
            old_hash, old_time = _bid_cooldowns[cooldown_key]
            if old_hash == bid_hash and now - old_time < 5:
                logger.info(f"Deduplicated bid for req_id={req_id} by user={user_id}")
                return safe_json_response({"ok": True, "note": "duplicate ignored"})
        
        _bid_cooldowns[cooldown_key] = (bid_hash, now)

        logger.info(f"Submitting bid for req_id={req_id} by {profile['name']} (ID: {user_id})")
        
        # Check if updating to provide better notification
        existing_bid = await db.get_user_bid(int(req_id), user_id)
        is_update = existing_bid is not None
        
        await db.upsert_bid(int(req_id), user_id, profile["name"], data)
        await db.log_activity(int(req_id), user_id, profile["name"], "bid_updated" if is_update else "bid_submitted", {"amount": data.get("amount")})
        
        # Add internal comment
        bid_data = {**data, "request_id": int(req_id), "manager_name": profile["name"]}
        bid_card = build_bid_card(bid_data)
        if is_update:
            comment_text = f"🔄 Ставка обновлена: <b>{data.get('amount')} {data.get('currency')}</b>"
            await db.add_comment(int(req_id), user_id, profile["name"], comment_text, "bid_update")
        else:
            await db.add_comment(int(req_id), user_id, profile["name"], bid_card, "bid")
        
        # Notify channel/discussion if possible
        settings = await db.get_settings()
        discussion_id = settings.get("discussion_id") or os.getenv("DISCUSSION_GROUP_ID")
        target_channel = settings.get("channel_id") or os.getenv("CHANNEL_ID")
        
        req = await db.get_request(int(req_id))
        bot = request.app["bot"]

        if discussion_id and req and target_channel:
            msg_id = req.get("channel_msg_id")
            if msg_id:
                notif_text = bid_card
                if is_update:
                    notif_text = f"🔄 <b>{profile['name']} обновил ставку</b>\n\nАктуальная ставка: <b>{data.get('amount')} {data.get('currency')}</b>\n#ставка"
                
                logger.info(f"Syncing bid to discussion: channel={target_channel}, msg={msg_id}")
                await sync_bid_to_discussion(bot, discussion_id, target_channel, msg_id, notif_text)
            else:
                await bot.send_message(chat_id=discussion_id, text=bid_card)

        # Notify the creator of the request
        creator_id = req.get("creator_id") if req else None
        logger.info(f"Notification check: creator_id={creator_id}, current_user={user_id}")
        
        if creator_id and int(creator_id) != user_id:
            try:
                notify_text = (
                    f"💰 <b>Новая ставка по вашей заявке #{int(req_id):05d}</b>\n"
                    f"📦 Груз: {req.get('cargo_name', '-')}\n"
                    f"📍 Маршрут: {req.get('route_from', '-')} → {req.get('route_to', '-')}\n\n"
                    f"💵 Сумма: <b>{data.get('amount')} {data.get('currency')}</b>\n"
                    f"👤 От: {profile['name']}\n\n"
                    f"Посмотреть подробности можно в Mini App."
                )
                await bot.send_message(chat_id=int(creator_id), text=notify_text, parse_mode="HTML")
                logger.info(f"Notification sent to creator {creator_id}")
            except Exception as e:
                logger.error(f"Failed to notify creator {creator_id}: {e}")
        
        return safe_json_response({"ok": True})
    except Exception as e:
        logger.error(f"api_bid error: {e}", exc_info=True)
        return safe_json_response({"error": "Internal error"})

async def api_comments(request):
    # Comments contain business correspondence — restrict to authenticated employees.
    profile, err = await check_auth(request)
    if err:
        return safe_json_response({"error": err, "auth_needed": True})

    try:
        req_id = int(request.query.get("req_id"))
    except (TypeError, ValueError):
        return web.json_response({"error": "req_id required (int)"}, status=400)

    comments = await db.get_comments(req_id)
    return safe_json_response({"comments": comments})

async def api_upload(request):
    profile, err = await check_auth(request)
    if err:
        return web.json_response({"error": err, "auth_needed": True}, status=401)
    user_id = profile.get("telegram_id")
    if not user_id:
        return web.json_response({"error": "Invalid identity"}, status=401)

    now = time.time()
    _gc_upload_counts(now)
    times = [t for t in _upload_counts.get(user_id, []) if now - t < 60]
    if len(times) >= 20: # Increased limit for photos
        return web.json_response({"error": "Rate limit (20 files/min)"}, status=429)
    times.append(now)
    _upload_counts[user_id] = times

    try:
        reader = await request.multipart()
        field = await reader.next()
        if not field or field.name != 'file':
            return web.json_response({"error": "No file field"}, status=400)

        filename = field.filename
        if not filename:
            return web.json_response({"error": "Empty filename"}, status=400)

        upload_dir = "uploads"
        if not os.path.exists(upload_dir):
            os.makedirs(upload_dir)

        # Generate a safe unique name
        import uuid
        ext = os.path.splitext(filename)[1]
        unique_name = f"{uuid.uuid4().hex}{ext}"
        file_path = os.path.join(upload_dir, unique_name)

        size = 0
        with open(file_path, 'wb') as f:
            while True:
                chunk = await field.read_chunk()
                if not chunk:
                    break
                size += len(chunk)
                f.write(chunk)
                if size > 20 * 1024 * 1024: # 20MB limit
                    return web.json_response({"error": "File too large (max 20MB)"}, status=413)

        file_type = field.headers.get('Content-Type', 'application/octet-stream')
        # Store in DB without request_id for now
        attachment_id = await db.add_attachment(None, filename, f"/uploads/{unique_name}", file_type, size)

        return web.json_response({
            "ok": True,
            "attachment_id": attachment_id,
            "url": f"/uploads/{unique_name}",
            "name": filename,
            "type": file_type
        })
    except Exception as e:
        logger.error(f"Upload error: {e}")
        return web.json_response({"error": "Upload failed"}, status=500)

async def index_handler(request):
    return web.FileResponse('webapp/index.html')

async def api_request_bids(request):
    """List bids for a specific request — sensitive competitor pricing data, employees only."""
    profile, err = await check_auth(request)
    if err:
        return safe_json_response({"error": err, "auth_needed": True})

    try:
        req_id = int(request.query.get("id"))
    except (TypeError, ValueError):
        return safe_json_response({"error": "Invalid id"})
    bids = await db.list_bids(req_id)
    return safe_json_response({"bids": bids})
async def api_list_tariffs(request):
    profile, err = await check_auth(request)
    if err: return safe_json_response({"error": err, "auth_needed": True})
    tariffs = await db.list_tariffs()
    return safe_json_response({"tariffs": tariffs})

async def api_upload_tariff(request):
    profile, err = await check_auth(request)
    if err or profile["role"] not in ["admin", "superuser"]:
        return safe_json_response({"error": "Forbidden"})

    try:
        reader = await request.multipart()
        # Field 1: title
        field_title = await reader.next()
        title = await field_title.text()
        
        # Field 2: file
        field_file = await reader.next()
        filename = field_file.filename
        
        upload_dir = "uploads"
        import uuid
        ext = os.path.splitext(filename)[1]
        unique_name = f"tariff_{uuid.uuid4().hex}{ext}"
        file_path = os.path.join(upload_dir, unique_name)
        
        size = 0
        with open(file_path, 'wb') as f:
            while True:
                chunk = await field_file.read_chunk()
                if not chunk: break
                size += len(chunk)
                f.write(chunk)
        
        file_type = field_file.headers.get('Content-Type', 'application/octet-stream')
        await db.add_tariff(title, filename, f"/uploads/{unique_name}", file_type, size, profile["name"])
        return safe_json_response({"ok": True})
    except Exception as e:
        logger.error(f"Tariff upload error: {e}")
        return safe_json_response({"error": "Upload failed"})

async def api_delete_tariff(request):
    profile, err = await check_auth(request)
    if err or profile["role"] not in ["admin", "superuser"]:
        return safe_json_response({"error": "Forbidden"})
    
    data = await request.json()
    tariff_id = data.get("id")
    await db.delete_tariff(int(tariff_id))
    return safe_json_response({"ok": True})

async def api_user_bid(request):
    profile, err = await check_auth(request)
    if err: return safe_json_response({"error": err, "auth_needed": True})
    user_id = profile.get("telegram_id")
    try:
        req_id = int(request.query.get("id"))
    except:
        return safe_json_response({"error": "Invalid id"})
    
    bid = await db.get_user_bid(req_id, user_id)
    return safe_json_response({"bid": bid})

async def verify_admin(init_data):
    """Helper to check if user is admin/superuser via init_data."""
    user_id, _ = extract_user_from_init_data(init_data)
    user = await db.get_user(user_id)
    return user and user.get('role') in ['admin', 'superuser']

async def api_stats(request):
    profile, err = await check_auth(request)
    if err or profile["role"] not in ["admin", "superuser"]:
        return safe_json_response({"error": "Forbidden"})
    
    try:
        days = int(request.query.get("days", 30))
    except (TypeError, ValueError):
        days = 30
    
    stats = await db.get_stats(days=days)
    return safe_json_response({"ok": True, "stats": stats})

async def api_logs(request):
    profile, err = await check_auth(request)
    if err or profile["role"] not in ["admin", "superuser"]:
        return safe_json_response({"error": "Forbidden"})
    
    logs = await db.get_recent_logs()
    return safe_json_response({"ok": True, "logs": logs})

async def api_user_rotate_key(request):
    data = await request.json()
    profile, err = await check_auth(request)
    if err or profile["role"] not in ["admin", "superuser"]:
        return safe_json_response({"error": "Forbidden"})

    user_id = data.get("user_id") or data.get("id")
    if not user_id:
        return safe_json_response({"error": "user_id required"})
    try:
        user_id = int(user_id)
    except (ValueError, TypeError):
        return safe_json_response({"error": "user_id must be int"})

    new_key_val = data.get("new_key")
    new_key = await db.rotate_user_key(user_id=user_id, new_key=new_key_val)
    if not new_key:
        return safe_json_response({"error": "User not found"})
    return safe_json_response({"ok": True, "new_key": new_key})

async def api_get_settings(request):
    """Settings include channel_id, ai_prompt, etc — restrict to employees."""
    profile, err = await check_auth(request)
    if err:
        return safe_json_response({"error": err, "auth_needed": True})

    settings = await db.get_settings()
    return safe_json_response({"ok": True, "settings": settings})

async def api_update_setting(request):
    data = await request.json()
    init_data = data.get("initData", "")
    if not verify_init_data(init_data, BOT_TOKEN):
        return web.json_response({"error": "Unauthorized"}, status=401)

    if not await verify_admin(init_data):
        return web.json_response({"error": "Forbidden"}, status=403)

    key = data.get("key")
    value = data.get("value")
    if key not in SETTABLE_KEYS:
        return web.json_response({"error": "Unknown setting"}, status=400)
    # Lists must stay lists; strings stay strings; cap reasonable size.
    if isinstance(value, list):
        if len(value) > 200:
            return web.json_response({"error": "List too long"}, status=400)
        value = [str(x)[:200] for x in value]
    elif isinstance(value, str):
        value = value[:8000]
    await db.update_setting(key, value)
    return safe_json_response({"ok": True})

async def api_ping_logistics(request: web.Request):
    try:
        # Posts to the public Telegram channel — gate to employees to prevent spam.
        profile, err = await check_auth(request)
        if err:
            return web.json_response({"error": err, "auth_needed": True}, status=401)

        data = await request.json()
        try:
            req_id = int(data.get('request_id'))
        except (TypeError, ValueError):
            return web.json_response({"error": "Invalid request_id"}, status=400)

        req_data = await db.get_request(req_id)
        if not req_data or not req_data.get("channel_msg_id"):
            return web.json_response({"error": "Сообщение в канале не найдено"}, status=404)

        settings = await db.get_settings()
        channel_id = settings.get("channel_id") or os.getenv("CHANNEL_ID")
        if not channel_id:
            return web.json_response({"error": "Не настроен канал"}, status=500)

        bot = request.app["bot"]
        await bot.send_message(
            chat_id=channel_id,
            reply_to_message_id=int(req_data["channel_msg_id"]),
            text=f"‼️ Уважаемые коллеги, заявка #{req_data['id']:04d} ({req_data['route_from']} ➔ {req_data['route_to']}) всё ещё актуальна! Ждём ваших ставок."
        )
        from utils.helpers import TZ
        await db.update_request(req_id, {"last_notified_at": datetime.now(TZ)})
        return web.json_response({"ok": True})
    except Exception as e:
        logger.error(f"Ping API error: {e}")
        return web.json_response({"error": "Internal error"}, status=500)

DICTIONARY_KEYS = {"incoterms", "ports", "border_crossings", "transport_subtypes", "transport_types", "regions", "currencies", "cancel_reasons", "sources"}
SETTABLE_KEYS = DICTIONARY_KEYS | {
    "ai_prompt_extra", "ai_strictness", "channel_id", "discussion_id", "reminder_interval"
}


async def api_get_dictionary(request):
    profile, err = await check_auth(request)
    if err:
        return safe_json_response({"error": err, "auth_needed": True})

    name = request.query.get("name")
    if name not in DICTIONARY_KEYS:
        return safe_json_response({"error": "Unknown dictionary"})
    settings = await db.get_settings()
    return safe_json_response({"ok": True, "items": settings.get(name, [])})

@web.middleware
async def _error_middleware(request, handler):
    """Convert common request-level errors to clean JSON 4xx instead of 500-stacktraces.

    Specifically: malformed JSON in POST bodies and aiohttp's HTTPException pass through.
    Anything else falls through to aiohttp's default handler.
    """
    try:
        return await handler(request)
    except web.HTTPException:
        raise
    except json.JSONDecodeError:
        return web.json_response({"error": "Invalid JSON body"}, status=400)
    except Exception as e:
        logger.exception(f"Unhandled error in {request.path}: {e}")
        return web.json_response({"error": "Internal error"}, status=500)


def setup_api(app):
    app.middlewares.append(_error_middleware)
    app.router.add_get("/", index_handler)
    app.router.add_get("/health", api_health)
    app.router.add_get("/api/users", api_users)
    app.router.add_get("/api/profile", api_profile)
    app.router.add_get("/api/requests", api_requests)
    app.router.add_get("/api/request_details", api_request_details)
    app.router.add_get("/api/request_bids", api_request_bids)
    app.router.add_get("/api/my_bids", api_my_bids)
    app.router.add_get("/api/my_requests", api_my_requests)
    app.router.add_post("/api/submit", api_submit)
    app.router.add_post("/api/bid", api_bid)
    app.router.add_post("/api/bid_cancel", api_bid_cancel)
    app.router.add_get("/api/settings", api_get_settings)
    app.router.add_get("/api/dictionary", api_get_dictionary)
    app.router.add_get("/api/stats", api_stats)
    app.router.add_get("/api/logs", api_logs)
    app.router.add_post("/api/settings/update", api_update_setting)
    app.router.add_post("/api/user_update", api_user_update)
    app.router.add_post("/api/user_create", api_user_create)
    app.router.add_post("/api/user_rotate_key", api_user_rotate_key)
    app.router.add_post("/api/login_with_key", api_login_with_key)
    app.router.add_post("/api/update_status", api_update_status)
    app.router.add_post("/api/ping_logistics", api_ping_logistics)
    app.router.add_post("/api/export_xlsx", api_export_xlsx)
    app.router.add_get("/api/comments", api_comments)
    app.router.add_post("/api/upload", api_upload)
    
    app.router.add_get("/api/tariffs", api_list_tariffs)
    app.router.add_post("/api/tariffs/upload", api_upload_tariff)
    app.router.add_post("/api/tariffs/delete", api_delete_tariff)
    app.router.add_get("/api/user_bid", api_user_bid)
    
    # Ensure directories exist before adding static routes
    for d in ["uploads", "logo"]:
        if not os.path.exists(d):
            os.makedirs(d)
            
    app.router.add_static("/uploads", "uploads")
    app.router.add_static("/logo", "logo")
    
    # CORS — restrict to the configured WEBAPP_URL origin (Telegram MiniApp).
    # Wildcard with credentials would be dangerous: any site could call our APIs in the
    # user's authenticated context. We rely on Telegram initData for CSRF-grade auth,
    # but defence-in-depth requires a strict origin allowlist.
    webapp_url = os.getenv("WEBAPP_URL", "").rstrip("/")
    allowed_origins = {}
    if webapp_url:
        allowed_origins[webapp_url] = aiohttp_cors.ResourceOptions(
            allow_credentials=True,
            expose_headers="*",
            allow_headers=("Content-Type", "Authorization"),
            allow_methods=("GET", "POST", "OPTIONS"),
        )
    else:
        logger.warning("WEBAPP_URL not configured — CORS will reject all cross-origin requests")
    cors = aiohttp_cors.setup(app, defaults=allowed_origins)
    for route in list(app.router.routes()):
        try:
            cors.add(route)
        except ValueError:
            # Some routes (e.g. static) may not support CORS — skip silently.
            pass
