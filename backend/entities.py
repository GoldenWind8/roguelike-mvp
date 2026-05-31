from dataclasses import dataclass

from backend.config import PLAYER_ATTACK_DAMAGE, PLAYER_DEFENSE


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
    defense: int = PLAYER_DEFENSE
    attack_damage: int = PLAYER_ATTACK_DAMAGE
    is_alive: bool = True

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "position": [self.position.x, self.position.y],
            "hp": self.hp,
            "max_hp": self.max_hp,
            "defense": self.defense,
            "attack_damage": self.attack_damage,
            "is_alive": self.is_alive,
        }


@dataclass
class Enemy:
    id: str
    name: str
    position: Position
    hp: int
    max_hp: int
    attack_damage: int
    defense: int
    is_alive: bool = True

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "position": [self.position.x, self.position.y],
            "hp": self.hp,
            "max_hp": self.max_hp,
            "attack_damage": self.attack_damage,
            "defense": self.defense,
            "is_alive": self.is_alive,
        }
