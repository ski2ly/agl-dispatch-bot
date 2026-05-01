import os
import logging
import asyncio
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import json
import time

logger = logging.getLogger(__name__)

class SheetsManager:
    def __init__(self):
        self.scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
        self.creds = None
        self.client = None
        self.sheet = None
        self._queue = asyncio.Queue()
        self._row_cache = {}
        self._cache_time = 0
        self._initialized = False

    async def initialize(self):
        """Initialize connection and start background worker."""
        try:
            creds_json = os.getenv("GOOGLE_SHEETS_CREDENTIALS")
            sheet_id = os.getenv("GOOGLE_SHEET_ID")
            sheet_name = os.getenv("GOOGLE_SHEET_NAME", "лист запросов")

            if not creds_json or not sheet_id:
                logger.warning("Sheets integration disabled (set GOOGLE_SHEETS_CREDENTIALS and GOOGLE_SHEET_ID to enable)")
                return

            creds_dict = json.loads(creds_json)
            # Fix potential escaped newlines in private_key
            if "private_key" in creds_dict:
                creds_dict["private_key"] = creds_dict["private_key"].replace("\\n", "\n")
            
            self.creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, self.scope)
            self.client = gspread.authorize(self.creds)
            self.sheet = self.client.open_by_key(sheet_id).worksheet(sheet_name)
            
            self._initialized = True
            asyncio.create_task(self.start_worker())
            logger.info(f"✅ Google Sheets initialized: {sheet_name}")
        except Exception as e:
            logger.error(f"Sheets init failed: {e}")

    async def sync_all_requests(self, requests_list):
        """Full re-sync of all requests."""
        if not self._initialized: return
        self._enqueue(self._sync_full_rebuild, requests_list)

    def _sync_full_rebuild(self, requests_list):
        """Clears sheet and refills it (Admin tool)."""
        # Keep header if exists
        header = self.sheet.row_values(1)
        self.sheet.clear()
        if header:
            self.sheet.append_row(header)
        
        rows = [self._req_to_row(r) for r in requests_list]
        if rows:
            self.sheet.append_rows(rows, value_input_option='USER_ENTERED')

    async def start_worker(self):
        """Background worker to process Sheets operations with rate limiting."""
        while True:
            func, args, kwargs = await self._queue.get()
            try:
                await asyncio.to_thread(func, *args, **kwargs)
                logger.debug(f"Sheets task completed: {func.__name__}")
            except Exception as e:
                logger.error(f"Sheets worker error in {func.__name__}: {e}")
            
            await asyncio.sleep(1.1)  # Quota protection (~55 req/min)
            self._queue.task_done()

    def _enqueue(self, func, *args, **kwargs):
        """Add a task to the queue (non-blocking)."""
        if not self._initialized: return
        self._queue.put_nowait((func, args, kwargs))

    def _req_to_row(self, req: dict):
        """Map request dict to a sheet row (38 columns)."""
        # Column A: ID (#0042)
        req_id = req.get("id", 0)
        id_str = f"#{int(req_id):04d}"
        
        # Mapping based on the existing analytical sheet structure
        row = [
            id_str,                                     # A: ID
            req.get("responsible", "-"),               # B: Менеджер
            req.get("status", "Открыта"),              # C: Статус
            req.get("regions", "-"),                   # D: Регион
            req.get("cargo_name", "-"),                # E: Наименование груза
            req.get("cargo_value", "-"),               # F: Стоимость груза
            req.get("hs_code", "-"),                   # G: ТН ВЭД
            req.get("route_from", "-"),                # H: Откуда
            req.get("route_to", "-"),                  # I: Куда
            req.get("transport_cat", "-"),             # J: Тип транспорта
            req.get("transport_sub", "-"),             # K: Подтип/Контейнер/Фура
            req.get("cargo_weight", "-"),              # L: Вес
            req.get("cargo_places", "-"),              # M: Места
            req.get("departure_date", "-"),            # N: Дата готовности
            req.get("urgency_type", "-"),              # O: Срочность
            req.get("delivery_terms", "-"),            # P: Incoterms
            req.get("route_type", "-"),                # Q: Маршрут (РФ/Турция)
            req.get("transit_rf", "-"),                # R: Транзит РФ
            req.get("container_type", "-"),            # S: Контейнер
            req.get("export_decl", "-"),               # T: Экспортная
            req.get("origin_cert", "-"),               # U: Серт. происх.
            req.get("border_crossing", "-"),           # V: Погранпереход
            req.get("glonass_seal", "-"),              # W: ГЛОНАСС
            req.get("departure_ports", "-"),           # X: Порты
            req.get("client_company", "-"),            # Y: Компания клиента
            req.get("contact_phone", "-"),             # Z: Телефон
            req.get("message_text", "-"),              # AA: Доп. инфо
            req.get("created_at", "-"),                # AB: Дата создания
            "", "", "", "", "", "", "", "", "", ""    # AC-AL: Reserved/Manager bids
        ]
        return row

    # PUBLIC ASYNC METHODS (These just enqueue the sync work)
    async def add_request(self, req: dict):
        row = self._req_to_row(req)
        self._enqueue(self._sync_add_request, row)
        self._cache_time = 0 # Invalidate cache

    async def update_status(self, req_id: int, status: str):
        self._enqueue(self._sync_update_cell, req_id, 3, status)

    async def add_bid(self, req_id: int, manager_name: str, amount: str, currency: str):
        self._enqueue(self._sync_add_bid, req_id, manager_name, amount, currency)

    # SYNC METHODS (Run in threads by the worker)
    def _sync_add_request(self, row):
        self.sheet.append_row(row, value_input_option='USER_ENTERED')

    def _sync_update_cell(self, req_id, col, val):
        row_num = self._find_row(req_id)
        if row_num:
            self.sheet.update_cell(row_num, col, val)

    def _sync_add_bid(self, req_id, manager_name, amount, currency):
        row_num = self._find_row(req_id)
        if not row_num: return
        
        # Simple static mapping for common managers or dynamic lookup
        col_num = self._get_manager_col(manager_name)
        if col_num:
            sym = '$' if currency == 'USD' else currency + ' '
            self.sheet.update_cell(row_num, col_num, f"{sym}{amount}")

    def _find_row(self, req_id):
        """Cached row lookup."""
        if time.time() - self._cache_time > 300:
            try:
                col_a = self.sheet.col_values(1)
                self._row_cache = {str(val).strip(): i + 1 for i, val in enumerate(col_a)}
                self._cache_time = time.time()
            except Exception as e:
                logger.error(f"Row cache refresh failed: {e}")
                return None

        search_val = f"#{int(req_id):04d}"
        return self._row_cache.get(search_val)

    def _get_manager_col(self, name):
        """Map manager name to columns L-P or beyond."""
        mapping = {"Нозим": 12, "Саидахмад": 13, "Арсен": 14, "Сардор": 15, "Константин": 16}
        return mapping.get(name)

sheets_manager = SheetsManager()

async def sync_request(req_data):
    """Global helper for server.py"""
    if not sheets_manager._initialized:
        await sheets_manager.initialize()
    await sheets_manager.add_request(req_data)
