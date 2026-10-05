from dataclasses import dataclass


@dataclass
class SyncedCommand:
    name: str
    id: int


async def test_commands_are_synced_only_when_they_change(bot, tmp_path):
    bot.settings.database_path = tmp_path / "bot.db"
    calls = []

    async def sync(*, guild=None):
        calls.append("sync")
        return [SyncedCommand("help", 1)]

    async def fetch_commands(*, guild=None):
        calls.append("fetch")
        return [SyncedCommand("help", 1)]

    bot.tree.sync, bot.tree.fetch_commands = sync, fetch_commands
    await bot.sync_commands()
    await bot.sync_commands()
    assert calls == ["sync", "fetch"] and bot.command_ids == {"help": 1}

    bot.tree.remove_command("help")
    await bot.sync_commands()
    assert calls[-1] == "sync"
