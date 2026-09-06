class UnknownGenreError(ValueError):
    """Raised when a genre name is not in the knowledge registry."""

    def __init__(self, name: str) -> None:
        self.genre = name
        super().__init__(f"Unknown genre: {name}")


class InvalidGenreTemplateError(ValueError):
    """Raised when a genre YAML file is missing required fields."""
