# Roguelike MMO — V1

A multiplayer turn-based roguelike game on a grid. Players join via browser, take turns moving and attacking, and see everything update in real time via WebSockets.

## Setup

```bash
# Create a virtual environment (skip if you already have .venv)
python3 -m venv .venv

# Activate it
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

## Run

```bash
uvicorn backend.main:app --host 0.0.0.0 --port 8000
```

Then open **two browser tabs** to [http://localhost:8000](http://localhost:8000), enter a name in each, and play.

## How to Play

- **Arrow keys** or **WASD** to move
- **Click** an adjacent player to attack
- Take turns — the turn banner shows whose turn it is
- Reduce your opponent's HP to 0 to win
