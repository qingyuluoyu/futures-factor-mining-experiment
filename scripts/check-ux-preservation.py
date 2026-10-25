import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("ux_existing_guard", ROOT / "scripts/check-stock-b23-preservation.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
CHANGED = frozenset({
    "backend/app/api/ai_chat.py", "backend/app/api/users.py", "backend/app/models/user_notify_setting.py",
    "backend/app/schemas/notify.py", "backend/app/services/ai/gateway.py",
    "backend/app/services/ai/providers/claude_provider.py", "backend/app/services/ai/providers/openai_provider.py",
    "frontend/next.config.ts", "frontend/tsconfig.json", "frontend/src/app/(auth)/login/page.tsx",
    "frontend/src/app/(main)/layout.tsx", "frontend/src/components/ai/floating-assistant.tsx",
    "frontend/src/components/layout/top-navbar.tsx", "frontend/src/components/market/ai-chat-panel.tsx",
    "frontend/src/components/profile/alert-settings-panel.tsx", "frontend/src/lib/ai-stream.ts",
    "frontend/src/stores/ai-chat.ts", "frontend/src/stores/ai-session.test.mjs",
    "frontend/src/stores/chat-response.test.mjs", "frontend/src/stores/market.ts",
    "frontend/src/stores/notifications.ts", "frontend/src/types/index.ts",
})
ADDED = frozenset({
    "scripts/check-ux-preservation.py", "scripts/verify-ux-runtime.py",
    "backend/app/services/ai/plain_chat.py", "backend/app/services/notify/preferences.py",
    "backend/migrations/20261021_013_notification_popup.sql", "backend/migrations/20261021_014_plain_chat.sql",
    "backend/scripts/migrate_notification_popup.py", "backend/scripts/migrate_plain_chat.py",
    "backend/scripts/verify_notification_popup_pg.py", "frontend/src/app/api/ai/chat/[[...path]]/route.ts",
    "frontend/src/components/landing/auth/login-dashboard-background.test.mjs",
    "frontend/src/components/landing/auth/login-dashboard-background.tsx", "frontend/src/lib/ai-stream.test.mjs",
    "frontend/src/lib/chat-proxy.test.mjs", "frontend/src/lib/notification-popup-policy.ts",
    "frontend/src/lib/ux-loading.test.mjs", "frontend/src/stores/notification-preferences.test.mjs",
    "frontend/src/stores/notification-preferences.ts",
})


def main():
    baseline = json.loads((ROOT / "logs/futures-preservation-b25-final.json").read_text(encoding="utf8"))
    current = module.guard.inventory(ROOT)
    delta = module.guard.differences(baseline["files"], current)
    unexpected = {"removed": delta["removed"], "added": sorted(set(delta["added"]) - ADDED),
                  "changed": sorted(set(delta["changed"]) - CHANGED)}
    report = {"status": "failed" if any(unexpected.values()) else "passed", "baseline": "futures-preservation-b25-final.json",
              "baseline_files": len(baseline["files"]), "scope": "explicit_shared_UX_exception_not_a_claim_of_zero_shared_changes",
              "actual_delta": delta, "unexpected": unexpected,
              "changed_hashes": {name: {"before": baseline["files"][name], "after": current[name]} for name in delta["changed"]}}
    (ROOT / "logs/ux-preservation-verified.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf8")
    print(json.dumps(report, ensure_ascii=True))
    return 1 if any(unexpected.values()) else 0


if __name__ == "__main__":
    raise SystemExit(main())
