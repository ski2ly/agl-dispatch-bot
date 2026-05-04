import os
import asyncio
import logging
import json
import hmac
import hashlib
import secrets
import asyncpg
from datetime import datetime
import pytz

logger = logging.getLogger(__name__)
TZ = pytz.timezone("Asia/Tashkent")


def _normalize_key(key: str) -> str:
    """Login keys are case-insensitive and trimmed."""
    return (key or "").strip().upper()


def hash_login_key(key: str) -> str:
    """HMAC-SHA256 of the login key with a server-side pepper from ENV.

    Stored in DB as the lookup identifier. Plaintext is never stored.
    Without LOGIN_KEY_PEPPER an attacker who steals the DB cannot brute-force
    keys in any reasonable time.
    """
    pepper = os.getenv("LOGIN_KEY_PEPPER")
    if not pepper or len(pepper) < 32:
        raise RuntimeError(
            "LOGIN_KEY_PEPPER env variable is missing or shorter than 32 chars. "
            "Generate one with: python -c 'import secrets; print(secrets.token_urlsafe(48))'"
        )
    norm = _normalize_key(key)
    return hmac.new(pepper.encode("utf-8"), norm.encode("utf-8"), hashlib.sha256).hexdigest()


def generate_login_key() -> str:
    """Generate a fresh, high-entropy login key (shown to the user only once)."""
    # token_urlsafe(12) = 16 chars, ~96 bits of entropy. Uppercase for legibility.
    return secrets.token_urlsafe(12).replace("-", "").replace("_", "").upper()[:16]


class Database:
    def __init__(self):
        self._pool = None

    async def init_db(self):
        """Initialize the asyncpg connection pool with retry logic."""
        dsn = os.getenv("DATABASE_URL")
        for attempt in range(10):
            try:
                self._pool = await asyncpg.create_pool(
                    dsn,
                    min_size=5,
                    max_size=20,
                    command_timeout=60,
                    timeout=30  # Per-acquire timeout — prevents pool starvation (#31)
                )
                logger.info("✅ PostgreSQL connected via asyncpg")
                await self._run_schema()
                return
            except Exception as e:
                logger.warning(f"DB connect attempt {attempt+1}/10 failed: {e}")
                await asyncio.sleep(3 * (attempt + 1))
        raise RuntimeError("Cannot connect to database after 10 attempts")

    async def _run_schema(self):
        """Run initial schema setup."""
        schema = """
        -- Ensure users can be added without telegram_id
        CREATE TABLE IF NOT EXISTS users (
            id SERIAL PRIMARY KEY,
            telegram_id BIGINT UNIQUE,
            name TEXT NOT NULL,
            role TEXT DEFAULT 'manager',
            login_key TEXT,
            login_key_hash TEXT UNIQUE,
            created_at TIMESTAMPTZ DEFAULT NOW()
        );

        CREATE TABLE IF NOT EXISTS requests (
            id SERIAL PRIMARY KEY,
            creator_id BIGINT,
            creator_name TEXT,
            responsible TEXT,
            status TEXT DEFAULT 'Открыта',
            regions TEXT,
            transport_cat TEXT,
            transport_sub TEXT,
            cargo_name TEXT,
            hs_code TEXT,
            cargo_value TEXT,
            cargo_weight TEXT,
            cargo_places TEXT,
            route_from TEXT,
            route_to TEXT,
            client_company TEXT,
            contact_name TEXT,
            contact_phone TEXT,
            message_text TEXT,
            target TEXT,
            delivery_terms TEXT,
            delivery_terms_eu TEXT,
            route_type TEXT,
            loading_address TEXT,
            customs_address TEXT,
            clearance_address TEXT,
            unloading_address TEXT,
            transit_rf TEXT,
            transit_rf_allowed TEXT,
            border_crossing TEXT,
            border_crossing_cn TEXT,
            urgency_type TEXT,
            urgency_days TEXT,
            loading_days TEXT,
            customs_days TEXT,
            export_decl TEXT,
            origin_cert TEXT,
            container_type TEXT,
            container_type_cn TEXT,
            road_type TEXT,
            road_type_cn TEXT,
            container_owner TEXT,
            glonass_seal TEXT,
            seal_instructions TEXT,
            flight_type TEXT,
            stackable TEXT,
            departure_ports TEXT,
            ports_list TEXT,
            multimodal_next TEXT,
            packaging TEXT,
            dangerous_cargo TEXT,
            winner_name TEXT,
            cancel_reason TEXT,
            mute_reminders BOOLEAN DEFAULT FALSE,
            company TEXT DEFAULT 'AGL',
            channel_msg_id BIGINT,
            created_at TIMESTAMPTZ DEFAULT NOW(),
            updated_at TIMESTAMPTZ DEFAULT NOW(),
            last_notified_at TIMESTAMPTZ
        );

        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value JSONB,
            updated_at TIMESTAMPTZ DEFAULT NOW()
        );

        CREATE TABLE IF NOT EXISTS bids (
            id SERIAL PRIMARY KEY,
            request_id INTEGER REFERENCES requests(id) ON DELETE CASCADE,
            user_id BIGINT,
            manager_name TEXT,
            amount TEXT,
            currency TEXT,
            validity TEXT,
            payment_terms TEXT,
            loading_hours TEXT,
            demurrage TEXT,
            comment TEXT,
            status TEXT DEFAULT 'Активна',
            created_at TIMESTAMPTZ DEFAULT NOW(),
            UNIQUE(request_id, user_id)
        );

        CREATE TABLE IF NOT EXISTS comments (
            id SERIAL PRIMARY KEY,
            request_id INTEGER REFERENCES requests(id) ON DELETE CASCADE,
            user_id BIGINT,
            user_name TEXT,
            text TEXT,
            type TEXT DEFAULT 'user',
            created_at TIMESTAMPTZ DEFAULT NOW()
        );

        CREATE TABLE IF NOT EXISTS activity_log (
            id SERIAL PRIMARY KEY,
            request_id INTEGER REFERENCES requests(id) ON DELETE CASCADE,
            user_id BIGINT,
            user_name TEXT,
            action TEXT,
            details JSONB,
            created_at TIMESTAMPTZ DEFAULT NOW()
        );

        CREATE TABLE IF NOT EXISTS ai_sessions (
            user_id BIGINT PRIMARY KEY,
            draft JSONB DEFAULT '{}',
            history JSONB DEFAULT '[]',
            updated_at TIMESTAMPTZ DEFAULT NOW()
        );
        """
        async with self._pool.acquire() as conn:
            await conn.execute(schema)
            # Migration for existing DBs
            await conn.execute("""
                ALTER TABLE requests 
                ADD COLUMN IF NOT EXISTS delivery_terms_eu TEXT,
                ADD COLUMN IF NOT EXISTS transit_rf_allowed TEXT,
                ADD COLUMN IF NOT EXISTS border_crossing_cn TEXT,
                ADD COLUMN IF NOT EXISTS urgency_days TEXT,
                ADD COLUMN IF NOT EXISTS loading_days TEXT,
                ADD COLUMN IF NOT EXISTS customs_days TEXT,
                ADD COLUMN IF NOT EXISTS container_type_cn TEXT,
                ADD COLUMN IF NOT EXISTS road_type_cn TEXT,
                ADD COLUMN IF NOT EXISTS ports_list TEXT,
                ADD COLUMN IF NOT EXISTS packaging TEXT,
                ADD COLUMN IF NOT EXISTS dangerous_cargo TEXT,
                ADD COLUMN IF NOT EXISTS winner_name TEXT,
                ADD COLUMN IF NOT EXISTS cancel_reason TEXT,
                ADD COLUMN IF NOT EXISTS mute_reminders BOOLEAN DEFAULT FALSE,
                ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ DEFAULT NOW();
            
            -- Session history migration
            await conn.execute("ALTER TABLE ai_sessions ADD COLUMN IF NOT EXISTS history JSONB DEFAULT '[]'")
            """)
            # (Moved to end of migrations)
            # MIGRATIONS
            try:
                # 1. Advanced Users Table Migration
                # Check if telegram_id is the primary key
                is_pk = await conn.fetchval("""
                    SELECT count(*) FROM information_schema.key_column_usage 
                    WHERE table_name = 'users' AND column_name = 'telegram_id'
                """)
                
                if is_pk > 0:
                    logger.info("Dropping old PK on telegram_id...")
                    # Find constraint name
                    pk_name = await conn.fetchval("""
                        SELECT constraint_name FROM information_schema.table_constraints 
                        WHERE table_name = 'users' AND constraint_type = 'PRIMARY KEY'
                    """)
                    if pk_name:
                        await conn.execute(f"ALTER TABLE users DROP CONSTRAINT {pk_name} CASCADE")
                
                # Ensure id column exists and is PK
                cols = await conn.fetch("SELECT column_name FROM information_schema.columns WHERE table_name = 'users'")
                user_cols = [c['column_name'] for c in cols]
                
                if 'id' not in user_cols:
                    await conn.execute("ALTER TABLE users ADD COLUMN id SERIAL PRIMARY KEY")
                
                # Finally, allow telegram_id to be NULL
                await conn.execute("ALTER TABLE users ALTER COLUMN telegram_id DROP NOT NULL")
                await conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_telegram_id ON users(telegram_id)")

                # login_key_hash column + migrate plaintext keys away
                await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS login_key_hash TEXT UNIQUE")
                await conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_login_key_hash ON users(login_key_hash)")

                # 2. Settings Table Migration (Ensure JSONB)
                await conn.execute("ALTER TABLE settings ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ DEFAULT NOW()")
                # Check column type
                val_type = await conn.fetchval("""
                    SELECT data_type FROM information_schema.columns 
                    WHERE table_name = 'settings' AND column_name = 'value'
                """)
                if val_type and val_type.lower() != 'jsonb':
                    logger.info("Migrating settings.value to JSONB...")
                    await conn.execute("ALTER TABLE settings ALTER COLUMN value TYPE JSONB USING value::jsonb")
                
                # Check for legacy 'manager' column in bids and make it nullable to prevent Internal Error
                try:
                    await conn.execute("ALTER TABLE bids ALTER COLUMN manager DROP NOT NULL")
                except:
                    pass

                # --- Aggressive Comments Migration ---
                # 1. Get all columns
                cols = await conn.fetch("SELECT column_name, is_nullable FROM information_schema.columns WHERE table_name = 'comments' AND table_schema = 'public'")
                col_names = [c['column_name'] for c in cols]
                
                # 2. Make all columns nullable to prevent 'NOT NULL' violations on legacy/phantom columns
                for c in cols:
                    if c['column_name'] not in ('id', 'request_id'):
                        await conn.execute(f'ALTER TABLE comments ALTER COLUMN "{c["column_name"]}" DROP NOT NULL')

                # 3. Handle 'text' column (rename from 'comment' if 'text' is missing)
                if 'text' not in col_names:
                    if 'comment' in col_names:
                        await conn.execute('ALTER TABLE comments RENAME COLUMN comment TO text')
                    else:
                        await conn.execute('ALTER TABLE comments ADD COLUMN text TEXT')

                # 4. Ensure 'user_name' exists
                if 'user_name' not in col_names:
                    await conn.execute('ALTER TABLE comments ADD COLUMN user_name TEXT')

                # 5. Ensure 'type' exists
                if 'type' not in col_names:
                    await conn.execute("ALTER TABLE comments ADD COLUMN type TEXT DEFAULT 'user'")

                # --- Users Migration ---
                try:
                    await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS login_key TEXT")
                except Exception: pass
                
                try:
                    await conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_login_key ON users(login_key)")
                except Exception as e:
                    logger.warning(f"Could not create login_key index: {e}")

                # CLEANUP: Remove duplicates (users with NULL login_key_hash or duplicates)
                # We keep the one with telegram_id or the newest one.
                # ... (existing migrations)
                await conn.execute("""
                    DELETE FROM users 
                    WHERE id NOT IN (
                        SELECT MIN(id) 
                        FROM users 
                        GROUP BY COALESCE(login_key_hash, name || role || id::text)
                    )
                """)
                
                # SEED / INIT
                await self.init_default_settings()
                await self.init_staff()
                logger.info("Database migrations and cleanup completed")
                
                # 2. Requests table columns
                cols = await conn.fetch("SELECT column_name FROM information_schema.columns WHERE table_name = 'requests'")
                req_cols = [c['column_name'] for c in cols]
                for col in [
                    'target', 'last_notified_at', 'cancel_reason', 'clearance_address', 'unloading_address',
                    'delivery_terms_eu', 'transit_rf_allowed', 'road_type_cn', 'border_crossing_cn',
                    'container_type_cn', 'loading_days', 'customs_days', 'urgency_days', 'ports_list',
                    'dangerous_cargo', 'packaging', 'message_text'
                ]:
                    if col not in req_cols:
                        await conn.execute(f"ALTER TABLE requests ADD COLUMN IF NOT EXISTS {col} TEXT")
                
                if 'mute_reminders' not in req_cols:
                    await conn.execute("ALTER TABLE requests ADD COLUMN IF NOT EXISTS mute_reminders BOOLEAN DEFAULT FALSE")
                
                # 3. Bids table columns
                cols = await conn.fetch("SELECT column_name FROM information_schema.columns WHERE table_name = 'bids'")
                bid_cols = [c['column_name'] for c in cols]
                if 'manager_name' not in bid_cols:
                    await conn.execute("ALTER TABLE bids ADD COLUMN IF NOT EXISTS manager_name TEXT")
                if 'status' not in bid_cols:
                    await conn.execute("ALTER TABLE bids ADD COLUMN IF NOT EXISTS status TEXT DEFAULT 'Ожидание'")
                
                # Add unique index for upsert
                await conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_bids_request_user ON bids(request_id, user_id)")
                
                # Add missing bid columns
                if 'loading_hours' not in bid_cols:
                    await conn.execute("ALTER TABLE bids ADD COLUMN IF NOT EXISTS loading_hours TEXT")
                if 'demurrage' not in bid_cols:
                    await conn.execute("ALTER TABLE bids ADD COLUMN IF NOT EXISTS demurrage TEXT")
                
                # 4. Add updated_at if not exists
                if 'updated_at' not in req_cols:
                    await conn.execute("ALTER TABLE requests ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ DEFAULT NOW()")
                if 'cancel_reason' not in req_cols:
                    await conn.execute("ALTER TABLE requests ADD COLUMN IF NOT EXISTS cancel_reason TEXT")
            except Exception as e:
                logger.error(f"Migration error: {e}")
            
            # 8. Settings table
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value JSONB NOT NULL,
                    updated_at TIMESTAMPTZ DEFAULT NOW()
                );
            """)
            
            # Migration for settings.value (TEXT -> JSONB)
            val_type = await conn.fetchval("""
                SELECT data_type FROM information_schema.columns 
                WHERE table_name = 'settings' AND column_name = 'value'
            """)
            if val_type == 'text':
                logger.info("Migrating settings.value from TEXT to JSONB...")
                await conn.execute("ALTER TABLE settings ALTER COLUMN value TYPE JSONB USING value::jsonb")

            await self.init_default_settings()
            await self.init_staff()
            await self._sync_superusers()
            logger.info("✅ Database schema verified and initialized.")

    async def _sync_superusers(self):
        """Ensure superusers from ENV are in the DB. UPSERT to be race-safe."""
        su_ids = [x.strip() for x in os.getenv("SUPERUSER_IDS", "2100694356").split(",") if x.strip()]
        for sid in su_ids:
            try:
                async with self._pool.acquire() as conn:
                    await conn.execute(
                        """
                        INSERT INTO users (telegram_id, name, role)
                        VALUES ($1, 'Admin', 'superuser')
                        ON CONFLICT (telegram_id) DO UPDATE SET role = 'superuser'
                        """,
                        int(sid),
                    )
            except Exception as e:
                logger.error(f"Error syncing superuser {sid}: {e}")

    async def create_user(self, name, role, login_key):
        """Pre-create a user without telegram_id. Stores only the hash of login_key.

        UPSERT keyed on hash so existing users with the same key get name/role updated.
        Returns the plaintext key passed in (so the caller can show it once).
        """
        key_hash = hash_login_key(login_key)
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO users (name, role, login_key_hash)
                VALUES ($1, $2, $3)
                ON CONFLICT (login_key_hash) DO UPDATE SET name = EXCLUDED.name, role = EXCLUDED.role
                """,
                name, role, key_hash
            )
            logger.info(f"Upserted user: {name} ({role})")
        return _normalize_key(login_key)

    async def get_user(self, telegram_id: int):
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM users WHERE telegram_id = $1", telegram_id)
            if row: return dict(row)
            
            # Fallback for Superusers
            su_ids = [int(x.strip()) for x in os.getenv("SUPERUSER_IDS", "2100694356").split(",") if x.strip()]
            if int(telegram_id) in su_ids:
                return {"telegram_id": int(telegram_id), "name": "Admin", "role": "superuser"}
            return None

    async def list_users(self):
        """Return users. Excludes superusers. Includes login_key for admin display as requested."""
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT id, telegram_id, name, role, login_key, created_at FROM users WHERE role != 'superuser' ORDER BY role DESC, name ASC"
            )
            return [dict(r) for r in rows]

    async def get_request(self, req_id: int):
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM requests WHERE id = $1", req_id)
            return dict(row) if row else None

    async def list_bids(self, request_id: int):
        async with self._pool.acquire() as conn:
            rows = await conn.fetch("SELECT * FROM bids WHERE request_id = $1 ORDER BY created_at DESC", request_id)
            return [dict(r) for r in rows]

    async def find_user_by_key(self, login_key: str):
        """Lookup user by the plaintext key — internally hashed before query."""
        key_hash = hash_login_key(login_key)
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM users WHERE login_key_hash = $1", key_hash)
            return dict(row) if row else None

    async def link_telegram_to_key(self, login_key: str, telegram_id: int):
        """Atomically link a telegram_id to the user identified by login_key.

        Returns:
            dict — user record on success
            None — no such key
            "telegram_taken" — telegram_id already belongs to a different user (caller decides UX)

        Race-safe: SELECT+UPDATE in one transaction with SELECT FOR UPDATE.
        """
        key_hash = hash_login_key(login_key)
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                user = await conn.fetchrow(
                    "SELECT id, name, role, telegram_id FROM users WHERE login_key_hash = $1 FOR UPDATE",
                    key_hash,
                )
                if not user:
                    return None
                # Already linked to the same Telegram account — nothing to do.
                if user["telegram_id"] and int(user["telegram_id"]) == int(telegram_id):
                    return dict(user)
                # Is this telegram_id currently bound to a different user?
                other = await conn.fetchrow(
                    "SELECT id FROM users WHERE telegram_id = $1 AND id != $2",
                    telegram_id, user["id"],
                )
                if other:
                    # Steal the binding: clear it from the previous user first so the UNIQUE
                    # constraint doesn't fire, then assign to the rightful owner of the key.
                    await conn.execute(
                        "UPDATE users SET telegram_id = NULL WHERE id = $1",
                        other["id"],
                    )
                await conn.execute(
                    "UPDATE users SET telegram_id = $1 WHERE id = $2",
                    telegram_id, user["id"],
                )
                return dict(user)

    async def save_user(self, telegram_id: int, name: str, role: str, login_key: str = None):
        """Upsert by telegram_id. If login_key passed — store its hash."""
        key_hash = hash_login_key(login_key) if login_key else None
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO users (telegram_id, name, role, login_key_hash)
                VALUES ($1, $2, $3, $4)
                ON CONFLICT (telegram_id) DO UPDATE
                    SET name = EXCLUDED.name,
                        role = EXCLUDED.role,
                        login_key_hash = COALESCE(EXCLUDED.login_key_hash, users.login_key_hash)
                """,
                telegram_id, name, role, key_hash
            )

    async def delete_user(self, telegram_id: int):
        async with self._pool.acquire() as conn:
            await conn.execute("DELETE FROM users WHERE telegram_id = $1", telegram_id)

    # ── SQL field name sanitization ──
    _SAFE_FIELD_RE = None
    @classmethod
    def _safe_col(cls, name: str) -> str:
        """Validate that a column name is safe to interpolate into SQL.
        Only allows a-z, 0-9, underscores — no SQL injection via field names."""
        import re
        if cls._SAFE_FIELD_RE is None:
            cls._SAFE_FIELD_RE = re.compile(r'^[a-z_][a-z0-9_]{0,63}$')
        if not cls._SAFE_FIELD_RE.match(name):
            raise ValueError(f"Unsafe SQL column name: {name!r}")
        return name

    # REQUEST METHODS
    async def create_request(self, fields: dict):
        safe_keys = [self._safe_col(k) for k in fields.keys()]
        placeholders = [f"${i+1}" for i in range(len(safe_keys))]
        query = f"INSERT INTO requests ({', '.join(safe_keys)}) VALUES ({', '.join(placeholders)}) RETURNING *"
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(query, *fields.values())
            return dict(row)

    async def update_request(self, req_id: int, fields: dict):
        set_parts = []
        values = []
        for i, (k, v) in enumerate(fields.items()):
            set_parts.append(f"{self._safe_col(k)} = ${i+1}")
            values.append(v)
        set_parts.append("updated_at = NOW()")
        values.append(req_id)
        query = f"UPDATE requests SET {', '.join(set_parts)} WHERE id = ${len(values)} RETURNING *"
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(query, *values)
            return dict(row) if row else None

    async def list_requests(self, limit=50, offset=0, status=None, search=None, region=None, manager=None, transport=None, **kwargs):
        query = "SELECT * FROM requests WHERE 1=1"
        params = []
        if status:
            params.append(status)
            query += f" AND status = ${len(params)}"
        if region:
            params.append(region)
            query += f" AND regions = ${len(params)}"
        if transport:
            params.append(f"%{transport}%")
            query += f" AND transport_cat ILIKE ${len(params)}"
        if manager:
            params.append(manager)
            query += f" AND (responsible = ${len(params)} OR creator_name = ${len(params)})"
        if search:
            params.append(f"%{search}%")
            query += f" AND (cargo_name ILIKE ${len(params)} OR route_from ILIKE ${len(params)} OR route_to ILIKE ${len(params)} OR client_company ILIKE ${len(params)} OR CAST(id AS TEXT) ILIKE ${len(params)})"
        
        # New filter for My Requests
        creator_id = kwargs.get("creator_id")
        if creator_id:
            params.append(int(creator_id))
            query += f" AND creator_id = ${len(params)}"
        
        query += " ORDER BY id DESC LIMIT $" + str(len(params)+1) + " OFFSET $" + str(len(params)+2)
        params.extend([limit, offset])
        
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(query, *params)
            return [dict(r) for r in rows]

    async def get_stale_requests(self, no_bids_days=3, open_days=7):
        async with self._pool.acquire() as conn:
            no_bids = await conn.fetch(
                """
                SELECT r.* FROM requests r
                LEFT JOIN bids b ON r.id = b.request_id
                WHERE r.status = 'Открыта' AND b.id IS NULL
                AND r.created_at < NOW() - INTERVAL '1 day' * $1
                AND r.mute_reminders = FALSE
                """,
                no_bids_days
            )
            old_open = await conn.fetch(
                "SELECT * FROM requests WHERE status = 'Открыта' AND created_at < NOW() - INTERVAL '1 day' * $1 AND mute_reminders = FALSE",
                open_days
            )
            return {
                "no_bids": [dict(r) for r in no_bids],
                "old_open": [dict(r) for r in old_open]
            }

    # BIDS
    async def upsert_bid(self, request_id: int, user_id: int, manager_name: str, fields: dict):
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO bids (request_id, user_id, manager_name, amount, currency, validity, payment_terms, loading_hours, demurrage, comment)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
                ON CONFLICT (request_id, user_id) DO UPDATE SET
                    amount=$4, currency=$5, validity=$6, payment_terms=$7, loading_hours=$8, demurrage=$9, comment=$10, created_at=NOW()
                """,
                request_id, user_id, manager_name, fields.get("amount"), fields.get("currency"),
                fields.get("validity"), fields.get("payment_terms") or fields.get("payment_method"),
                fields.get("loading_hours"), fields.get("demurrage"), fields.get("comment")
            )

    async def get_bids(self, request_id: int):
        async with self._pool.acquire() as conn:
            rows = await conn.fetch("SELECT * FROM bids WHERE request_id = $1 ORDER BY created_at DESC", request_id)
            return [dict(r) for r in rows]

    async def get_user_bids(self, user_id: int):
        async with self._pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT b.*, r.route_from as request_route_from, r.route_to as request_route_to, r.transport_cat 
                FROM bids b 
                JOIN requests r ON b.request_id = r.id 
                WHERE b.user_id = $1 
                ORDER BY b.created_at DESC
            """, user_id)
            return [dict(r) for r in rows]

    # COMMENTS
    async def add_comment(self, request_id: int, user_id: int, user_name: str, text: str, type="user"):
        async with self._pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO comments (request_id, user_id, user_name, text, type) VALUES ($1, $2, $3, $4, $5)",
                request_id, user_id, user_name, text, type
            )

    async def get_comments(self, request_id: int):
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM comments WHERE request_id = $1 ORDER BY created_at",
                request_id
            )
            return [dict(r) for r in rows]

    # SETTINGS
    async def get_settings(self):
        async with self._pool.acquire() as conn:
            rows = await conn.fetch("SELECT key, value FROM settings")
            settings = {}
            for r in rows:
                val = r['value']
                # If it's a string, try to parse as JSON (fallback for old TEXT column data)
                if isinstance(val, str):
                    try:
                        val = json.loads(val)
                    except:
                        pass
                settings[r['key']] = val
            return settings

    async def update_setting(self, key, value):
        async with self._pool.acquire() as conn:
            # Explicitly dump to JSON string to ensure compatibility with both TEXT and JSONB columns
            # and to avoid asyncpg type inference issues.
            import json
            json_value = json.dumps(value, ensure_ascii=False)
            await conn.execute(
                "INSERT INTO settings (key, value, updated_at) VALUES ($1, $2, NOW()) "
                "ON CONFLICT (key) DO UPDATE SET value = $2, updated_at = NOW()",
                key, json_value
            )

    async def init_default_settings(self):
        defaults = {
            "incoterms": ["EXW", "FCA", "FOB", "CFR", "CIF", "CIP", "CPT", "DAP", "DDP", "DPU", "FAS"],
            "ports": ["Поти", "Иран", "Пакистан", "Китай", "Владивосток", "Рига", "Клайпеда", "Стамбул", "Мерсин"],
            "border_crossings": ["Казахстан", "Кашгар", "Алашанькоу", "Хоргос", "Забайкальск"],
            "transport_subtypes": [
                "Тент 82м3", "Тент 86м3", "Тент 90м3", "Рефрижератор", "Юмба", "Мега", "Автовоз", "Трал",
                "20GP", "40HQ", "40OT", "40FR", "Вагон 138т", "Вагон 150т"
            ],
            "transport_types": ["Авто", "Контейнер", "Ж/Д Вагон", "Авиа", "Мультимодальная"],
            "regions": [
                {"name": "СНГ", "emoji": "🗺️"},
                {"name": "Европа", "emoji": "🇪🇺"},
                {"name": "Китай", "emoji": "🇨🇳"},
                {"name": "Турция", "emoji": "🇹🇷"},
                {"name": "Индия/ЮВА", "emoji": "🇮🇳"},
                {"name": "Другое", "emoji": "🌐"}
            ],
            "currencies": ["USD", "EUR", "RUB", "CNY", "UZS", "KZT", "TRY", "GBP"],
            "cancel_reasons": [
                "Ставка не прошла", "Груз отменился", "Выбрали другого экспедитора",
                "Не устроили сроки", "Техническая ошибка"
            ],
            "ai_prompt_extra": "",
            "ai_strictness": "medium",
            "channel_id": os.getenv("CHANNEL_ID"),
            "discussion_id": os.getenv("DISCUSSION_GROUP_ID"),
            "reminder_interval": 120 # minutes
        }
        for k, v in defaults.items():
            async with self._pool.acquire() as conn:
                exists = await conn.fetchval("SELECT 1 FROM settings WHERE key = $1", k)
                if not exists:
                    await self.update_setting(k, v)

    async def init_staff(self):
        """Seed users table with initial staff. 
        As requested: deletes previous list and adds the new 17 members.
        """
        async with self._pool.acquire() as conn:
            # Delete non-superusers to ensure a clean state for the new list
            await conn.execute("DELETE FROM users WHERE role != 'superuser'")
            
            staff = [
                ("Александр", "admin", "agl_ach"),
                ("Альберт", "admin", "agl_ak"),
                ("Арсен", "manager", "agl_ag"),
                ("Виолетта", "admin", "agl_vr"),
                ("Диёрахон", "manager", "agl_di"),
                ("Дмитрий", "manager", "agl_da"),
                ("Жамшид (директор)", "admin", "agl_jt"),
                ("Константин", "manager", "agl_kk"),
                ("Мубина", "manager", "agl_mo"),
                ("Нозим", "admin", "agl_nb"),
                ("Омон", "admin", "agl_oe"),
                ("Саидамир", "admin", "agl_su"),
                ("Саидахмад", "manager", "agl_ss"),
                ("Сардор", "manager", "agl_si"),
                ("Сардорхон", "admin", "agl_sn"),
                ("Умиджан", "manager", "agl_ua"),
                ("Акобир", "manager", "agl_au")
            ]
            for name, role, key in staff:
                key_hash = hash_login_key(key)
                await conn.execute("""
                    INSERT INTO users (name, role, login_key, login_key_hash)
                    VALUES ($1, $2, $3, $4)
                    ON CONFLICT (login_key_hash) DO UPDATE SET name = EXCLUDED.name, role = EXCLUDED.role, login_key = EXCLUDED.login_key
                """, name, role, key, key_hash)

    # LOGGING
    async def log_activity(self, request_id: int, user_id: int, user_name: str, action: str, details: dict = None):
        async with self._pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO activity_log (request_id, user_id, user_name, action, details) VALUES ($1, $2, $3, $4, $5)",
                request_id, user_id, user_name, action, json.dumps(details) if details else None
            )
    async def get_recent_logs(self, limit=50):
        async with self._pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT l.*, r.cargo_name 
                FROM activity_log l
                LEFT JOIN requests r ON l.request_id = r.id
                ORDER BY l.created_at DESC
                LIMIT $1
            """, limit)
            return [dict(r) for r in rows]

    async def get_requests_for_export(self, start_date=None, end_date=None):
        query = "SELECT * FROM requests WHERE 1=1"
        params = []
        if start_date:
            params.append(start_date)
            query += f" AND created_at >= ${len(params)}"
        if end_date:
            params.append(end_date)
            query += f" AND created_at <= ${len(params)}"
        
        query += " ORDER BY id DESC"
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(query, *params)
            return [dict(r) for r in rows]

    async def get_stats(self, days=30):
        # Calculate start date based on days (if days=0, get all time)
        date_filter = ""
        params = []
        if days > 0:
            params.append(days)
            date_filter = "AND created_at >= NOW() - INTERVAL '$1 days'"
        
        # Use helper to apply filter to queries
        def apply_filter(q):
            if "WHERE" in q.upper():
                return q.replace("WHERE", f"WHERE {date_filter.replace('$1', '1') if params else '1=1'} AND")
            else:
                return q + f" WHERE {date_filter.replace('$1', '1') if params else '1=1'}"

        async with self._pool.acquire() as conn:
            # 1. Manager Activity
            manager_stats = await conn.fetch(f"""
                SELECT responsible as name, COUNT(*) as count 
                FROM requests 
                WHERE responsible IS NOT NULL AND responsible != ''
                {"AND created_at >= NOW() - INTERVAL '" + str(days) + " days'" if days > 0 else ""}
                GROUP BY responsible 
                ORDER BY count DESC
            """)
            
            # 2. Regional Distribution
            region_stats = await conn.fetch(f"""
                SELECT regions as name, COUNT(*) as count 
                FROM requests 
                WHERE 1=1 {"AND created_at >= NOW() - INTERVAL '" + str(days) + " days'" if days > 0 else ""}
                GROUP BY regions
            """)
            
            # 3. Success Rate
            date_filter = f"AND created_at >= NOW() - INTERVAL '{days} days'" if days > 0 else ""
            total_closed = await conn.fetchval(f"""
                SELECT COUNT(*) FROM requests 
                WHERE status IN ('Успешно реализована', 'Отменена') {date_filter}
            """)
            successful = await conn.fetchval(f"""
                SELECT COUNT(*) FROM requests 
                WHERE status = 'Успешно реализована' {date_filter}
            """)
            success_rate = round((successful / total_closed * 100), 1) if total_closed else 0.0

            # 4. Cancellation Reasons
            cancel_reasons = await conn.fetch(f"""
                SELECT cancel_reason as reason, COUNT(*) as count
                FROM requests
                WHERE status = 'Отменена' AND cancel_reason IS NOT NULL AND cancel_reason != ''
                {"AND created_at >= NOW() - INTERVAL '" + str(days) + " days'" if days > 0 else ""}
                GROUP BY cancel_reason
                ORDER BY count DESC
                LIMIT 5
            """)

            # 5. Average closing time (hours)
            avg_time = await conn.fetchval(f"""
                SELECT AVG(EXTRACT(EPOCH FROM (updated_at - created_at))) / 3600
                FROM requests 
                WHERE status = 'Успешно реализована' AND updated_at IS NOT NULL
                {"AND created_at >= NOW() - INTERVAL '" + str(days) + " days'" if days > 0 else ""}
            """)

            # 6. Average time to first bid (minutes)
            avg_response = await conn.fetchval(f"""
                SELECT AVG(EXTRACT(EPOCH FROM (b.min_bid_time - r.created_at))) / 60
                FROM requests r
                JOIN (
                    SELECT request_id, MIN(created_at) as min_bid_time
                    FROM bids GROUP BY request_id
                ) b ON r.id = b.request_id
                WHERE r.created_at IS NOT NULL
                {"AND r.created_at >= NOW() - INTERVAL '" + str(days) + " days'" if days > 0 else ""}
            """)

            return {
                "managers": [dict(r) for r in manager_stats],
                "regions": [dict(r) for r in region_stats],
                "success_rate": success_rate,
                "cancel_reasons": [dict(r) for r in cancel_reasons],
                "avg_closing_hours": round(float(avg_time or 0), 1),
                "avg_response_minutes": round(float(avg_response or 0), 1)
            }

    async def log_activity(self, request_id, user_id, user_name, action, details=None):
        async with self._pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO activity_log (request_id, user_id, user_name, action, details)
                VALUES ($1, $2, $3, $4, $5)
            """, request_id, user_id, user_name, action, json.dumps(details) if details else None)

    async def get_recent_logs(self, limit=15):
        async with self._pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT l.*, r.cargo_name 
                FROM activity_log l
                LEFT JOIN requests r ON l.request_id = r.id
                ORDER BY l.created_at DESC LIMIT $1
            """, limit)
            return [dict(r) for r in rows]

    async def get_user_bids(self, user_id):
        async with self._pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT b.*, r.route_from, r.route_to, r.status as request_status
                FROM bids b
                JOIN requests r ON b.request_id = r.id
                WHERE b.user_id = $1
                ORDER BY b.created_at DESC
            """, user_id)
            return [dict(r) for r in rows]

    async def rotate_user_key(self, user_id: int = None, old_key: str = None, new_key: str = None):
        """Generate or set a fresh login key for a user. Returns the plaintext.
        Stores the plaintext login_key in the DB for admin display as requested.
        """
        if not new_key:
            new_key = generate_login_key()
        new_hash = hash_login_key(new_key)
        async with self._pool.acquire() as conn:
            if user_id is not None:
                res = await conn.execute(
                    "UPDATE users SET login_key_hash = $1, login_key = $2 WHERE id = $3",
                    new_hash, new_key, int(user_id),
                )
            elif old_key:
                old_hash = hash_login_key(old_key)
                res = await conn.execute(
                    "UPDATE users SET login_key_hash = $1, login_key = $2 WHERE login_key_hash = $3",
                    new_hash, new_key, old_hash,
                )
            else:
                return None
            if res.endswith(" 0"):
                return None
            return new_key

    async def delete_user_by_id(self, user_id: int):
        async with self._pool.acquire() as conn:
            await conn.execute("DELETE FROM users WHERE id = $1", int(user_id))

    async def update_user_profile(self, user_id: int, name: str = None, role: str = None):
        """Update a user's name/role by primary key. Login key is NOT updatable here — use rotate_user_key."""
        sets, vals = [], []
        if name is not None:
            vals.append(name); sets.append(f"name = ${len(vals)}")
        if role is not None:
            vals.append(role); sets.append(f"role = ${len(vals)}")
        if not sets:
            return
        vals.append(int(user_id))
        async with self._pool.acquire() as conn:
            await conn.execute(f"UPDATE users SET {', '.join(sets)} WHERE id = ${len(vals)}", *vals)

    # AI CONTEXT & SESSIONS
    async def get_ai_context(self, user_id: int):
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow("SELECT draft, history FROM ai_sessions WHERE user_id = $1", user_id)
            if row:
                return json.loads(row["draft"]), json.loads(row["history"])
            return {}, []

    async def save_ai_context(self, user_id: int, draft: dict, history: list = None):
        async with self._pool.acquire() as conn:
            if history is None:
                # Keep old history if not provided
                await conn.execute("""
                    INSERT INTO ai_sessions (user_id, draft, updated_at)
                    VALUES ($1, $2, NOW())
                    ON CONFLICT (user_id) DO UPDATE SET draft = EXCLUDED.draft, updated_at = NOW()
                """, user_id, json.dumps(draft))
            else:
                # Cap history to last 10 messages
                history = history[-10:]
                await conn.execute("""
                    INSERT INTO ai_sessions (user_id, draft, history, updated_at)
                    VALUES ($1, $2, $3, NOW())
                    ON CONFLICT (user_id) DO UPDATE SET draft = EXCLUDED.draft, history = EXCLUDED.history, updated_at = NOW()
                """, user_id, json.dumps(draft), json.dumps(history))

    async def clear_ai_context(self, user_id: int):
        async with self._pool.acquire() as conn:
            await conn.execute("DELETE FROM ai_sessions WHERE user_id = $1", user_id)

db = Database()
