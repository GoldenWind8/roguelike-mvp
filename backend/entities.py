from dataclasses import dataclass, field


@dataclass
class Position:
    x: int
    y: int


@dataclass
class Player:
    id: str
    name: str
    position: Position
    hp: int
    max_hp: int
    is_alive: bool = True

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "position": [self.position.x, self.position.y],
            "hp": self.hp,
            "max_hp": self.max_hp,
            "is_alive": self.is_alive,
        }
