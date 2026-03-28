"""
═══════════════════════════════════════════════════════════════════════════════
API_LOGS STORAGE — Persistent JSON Storage for Sessions
═══════════════════════════════════════════════════════════════════════════════

Handles:
  - Creating new sessions with initialized API_LOGS
  - Loading/saving sessions to JSON files
  - Listing and filtering sessions
"""

import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any, List


class APILogsStore:
    """
    Persistent storage for API_LOGS using JSON files.

    Each session is stored as a separate JSON file.
    """

    def __init__(self, storage_dir: str = "./api_logs_storage"):
        self.storage_dir = Path(storage_dir)
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self._cache: Dict[str, Dict] = {}

    def create_session(
        self,
        partner: str,
        session_id: str = None,
        initial_data: Dict[str, Any] = None,
    ) -> tuple[str, Dict[str, Any]]:
        """
        Create a new session with initialized API_LOGS.

        Args:
            partner: Partner name (CRED, PAYTM, etc.)
            session_id: Optional session ID (auto-generated if not provided)
            initial_data: Optional initial data to merge into API_LOGS

        Returns:
            (session_id, api_logs)
        """
        partner = partner.upper()

        if not session_id:
            session_id = f"{partner}_{uuid.uuid4().hex[:8]}"

        # Initialize API_LOGS with defaults
        api_logs = {
            # Session metadata
            "SESSION_ID": session_id,
            "PARTNER": partner,
            "REQID": f"{partner}_{uuid.uuid4().hex[:12]}",
            "CRN": f"CRN_{uuid.uuid4().hex[:8]}",

            # Loan defaults
            "LOAN_AMOUNT": 50000,
            "TENURE_MONTHS": 12,
            "LOAN_TYPE": "personal",

            # Timestamps
            "CREATED_AT": datetime.now().isoformat(),
            "LAST_UPDATED": datetime.now().isoformat(),

            # Execution tracking
            "EXECUTION_TRACE": [],
            "FLOW_STATE": "initialized",
        }

        # Merge initial data
        if initial_data:
            for key, value in initial_data.items():
                api_logs[key.upper()] = value

        # Save to file
        self.save_session(session_id, api_logs)

        return session_id, api_logs

    def load_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        """
        Load session from file.

        Returns None if session doesn't exist.
        """
        # Check cache first
        if session_id in self._cache:
            return self._cache[session_id].copy()

        # Load from file
        file_path = self.storage_dir / f"{session_id}.json"
        if not file_path.exists():
            return None

        try:
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self._cache[session_id] = data
            return data.copy()
        except Exception as e:
            print(f"Error loading session {session_id}: {e}")
            return None

    def save_session(self, session_id: str, api_logs: Dict[str, Any]):
        """
        Save session to file.
        """
        api_logs["LAST_UPDATED"] = datetime.now().isoformat()

        file_path = self.storage_dir / f"{session_id}.json"
        try:
            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(api_logs, f, indent=2, default=str)
            self._cache[session_id] = api_logs.copy()
        except Exception as e:
            print(f"Error saving session {session_id}: {e}")

    def delete_session(self, session_id: str) -> bool:
        """Delete a session."""
        file_path = self.storage_dir / f"{session_id}.json"
        try:
            if file_path.exists():
                file_path.unlink()
            self._cache.pop(session_id, None)
            return True
        except Exception:
            return False

    def list_sessions(self, partner: str = None) -> List[Dict[str, Any]]:
        """
        List all sessions, optionally filtered by partner.

        Returns list of session summaries (not full API_LOGS).
        """
        sessions = []

        for file_path in self.storage_dir.glob("*.json"):
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    data = json.load(f)

                # Filter by partner if specified
                if partner and data.get("PARTNER") != partner.upper():
                    continue

                sessions.append({
                    "session_id": data.get("SESSION_ID"),
                    "partner": data.get("PARTNER"),
                    "customer_name": data.get("FULL_NAME"),
                    "loan_amount": data.get("LOAN_AMOUNT"),
                    "flow_state": data.get("FLOW_STATE"),
                    "created_at": data.get("CREATED_AT"),
                    "last_updated": data.get("LAST_UPDATED"),
                })
            except Exception:
                continue

        # Sort by last updated (newest first)
        sessions.sort(key=lambda x: x.get("last_updated", ""), reverse=True)

        return sessions

    def session_exists(self, session_id: str) -> bool:
        """Check if session exists."""
        file_path = self.storage_dir / f"{session_id}.json"
        return file_path.exists()

    def update_field(self, session_id: str, field: str, value: Any) -> bool:
        """Update a single field in API_LOGS."""
        api_logs = self.load_session(session_id)
        if not api_logs:
            return False

        api_logs[field.upper()] = value
        self.save_session(session_id, api_logs)
        return True

    def update_fields(self, session_id: str, fields: Dict[str, Any]) -> bool:
        """Update multiple fields in API_LOGS."""
        api_logs = self.load_session(session_id)
        if not api_logs:
            return False

        for key, value in fields.items():
            api_logs[key.upper()] = value

        self.save_session(session_id, api_logs)
        return True


# ═══════════════════════════════════════════════════════════════════════════════
# EXPORTS
# ═══════════════════════════════════════════════════════════════════════════════

__all__ = ["APILogsStore"]
