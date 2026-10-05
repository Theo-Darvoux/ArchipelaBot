from discord import app_commands


class UserError(app_commands.AppCommandError):
    """An error whose message is meant for the user who ran the command."""
