let ws = null;
let myPlayerId = null;
let gameState = null;

const PLAYER_COLORS = ["#4ecca3", "#e94560", "#7ec8e3", "#ffd700"];
const PLAYER_ICONS = ["⚔", "☠", "♦", "♣"];

function connect() {
    const protocol = location.protocol === "https:" ? "wss:" : "ws:";
    ws = new WebSocket(`${protocol}//${location.host}/ws`);

    ws.onopen = () => {
        document.getElementById("connection-status").textContent = "Connected";
        document.getElementById("connection-status").className = "status-connected";
    };

    ws.onmessage = (event) => {
        const msg = JSON.parse(event.data);
        switch (msg.type) {
            case "join_ack":
                handleJoinAck(msg);
                break;
            case "state_update":
                handleStateUpdate(msg);
                break;
            case "error":
                appendEvent("error", msg.message);
                break;
        }
    };

    ws.onclose = () => {
        document.getElementById("connection-status").textContent = "Disconnected";
        document.getElementById("connection-status").className = "status-disconnected";
    };
}

function joinGame() {
    const nameInput = document.getElementById("player-name");
    const name = nameInput.value.trim() || "Anonymous";
    if (!ws || ws.readyState !== WebSocket.OPEN) return;
    ws.send(JSON.stringify({ type: "join", name }));
}

function handleJoinAck(msg) {
    myPlayerId = msg.player_id;
    gameState = msg.state;
    document.getElementById("join-screen").style.display = "none";
    document.getElementById("game-screen").style.display = "block";
    initGrid();
    renderAll();
    appendEvent("join", "You joined as " + msg.player_id);
}

function handleStateUpdate(msg) {
    gameState = msg.state;
    renderAll();
    if (msg.events) {
        msg.events.forEach((e) => appendEventFromServer(e));
    }
}

function initGrid() {
    const container = document.getElementById("grid-container");
    container.innerHTML = "";
    for (let y = 0; y < 10; y++) {
        for (let x = 0; x < 10; x++) {
            const cell = document.createElement("div");
            cell.className = "cell";
            cell.dataset.x = x;
            cell.dataset.y = y;
            cell.onclick = () => handleCellClick(x, y);
            container.appendChild(cell);
        }
    }
}

function renderAll() {
    if (!gameState) return;
    renderGrid();
    renderPlayers();
    renderTurnBanner();
}

function renderGrid() {
    const cells = document.querySelectorAll(".cell");
    const wallSet = new Set(gameState.walls.map((w) => w[0] + "," + w[1]));
    const playerIndex = getPlayerIndex();

    cells.forEach((cell) => {
        const x = parseInt(cell.dataset.x);
        const y = parseInt(cell.dataset.y);
        const key = x + "," + y;

        cell.className = "cell";
        cell.innerHTML = "";

        if (wallSet.has(key)) {
            cell.classList.add("cell-wall");
            cell.textContent = "█";
            cell.style.color = "#333";
            return;
        }

        const occupantId = gameState.grid[y][x];
        if (occupantId) {
            const player = gameState.players[occupantId];
            if (!player) return;
            const idx = playerIndex[occupantId] || 0;
            const color = PLAYER_COLORS[idx % PLAYER_COLORS.length];
            const icon = PLAYER_ICONS[idx % PLAYER_ICONS.length];

            cell.classList.add("cell-player");
            if (occupantId === gameState.current_turn) {
                cell.classList.add("cell-current");
            }
            if (occupantId === myPlayerId) {
                cell.classList.add("cell-me");
            }

            const iconEl = document.createElement("div");
            iconEl.className = "player-icon";
            iconEl.textContent = icon;
            iconEl.style.color = color;
            cell.appendChild(iconEl);

            const hpBar = document.createElement("div");
            hpBar.className = "hp-bar";
            const hpFill = document.createElement("div");
            hpFill.className = "hp-fill";
            const pct = (player.hp / player.max_hp) * 100;
            hpFill.style.width = pct + "%";
            hpFill.style.background = pct > 50 ? "#4ecca3" : pct > 25 ? "#ff8c42" : "#e94560";
            hpBar.appendChild(hpFill);
            cell.appendChild(hpBar);
        }
    });
}

function getPlayerIndex() {
    const index = {};
    const ids = Object.keys(gameState.players).sort();
    ids.forEach((id, i) => { index[id] = i; });
    return index;
}

function renderPlayers() {
    const list = document.getElementById("players-list");
    list.innerHTML = "";
    const playerIndex = getPlayerIndex();

    Object.values(gameState.players).forEach((p) => {
        const li = document.createElement("li");
        const idx = playerIndex[p.id] || 0;
        const color = PLAYER_COLORS[idx % PLAYER_COLORS.length];
        const icon = PLAYER_ICONS[idx % PLAYER_ICONS.length];
        const isMe = p.id === myPlayerId;
        const isTurn = p.id === gameState.current_turn;

        li.innerHTML = `
            <span style="color:${color}">${icon} ${p.name}${isMe ? " (you)" : ""}${isTurn ? " ◀" : ""}</span>
            <span>${p.hp}/${p.max_hp} HP</span>
        `;
        if (!p.is_alive) li.classList.add("player-dead");
        list.appendChild(li);
    });
}

function renderTurnBanner() {
    const banner = document.getElementById("turn-banner");
    if (!gameState.started) {
        banner.textContent = "Waiting for players...";
        banner.className = "not-your-turn";
        return;
    }

    const gameOverEvent = checkGameOver();
    if (gameOverEvent) {
        banner.textContent = gameOverEvent;
        banner.className = "game-over-banner";
        return;
    }

    if (gameState.current_turn === myPlayerId) {
        banner.textContent = "Your turn!";
        banner.className = "your-turn";
    } else {
        const current = gameState.players[gameState.current_turn];
        const name = current ? current.name : "???";
        banner.textContent = name + "'s turn";
        banner.className = "not-your-turn";
    }
}

function checkGameOver() {
    const living = Object.values(gameState.players).filter((p) => p.is_alive);
    if (gameState.started && living.length <= 1 && Object.keys(gameState.players).length >= 2) {
        if (living.length === 1) {
            return living[0].name + " wins!";
        }
        return "Draw!";
    }
    return null;
}

function sendMove(dx, dy) {
    if (!ws || !myPlayerId || !gameState) return;
    if (gameState.current_turn !== myPlayerId) return;
    ws.send(JSON.stringify({
        type: "action",
        action_type: "move",
        direction: [dx, dy],
    }));
}

function sendAttack(targetId) {
    if (!ws || !myPlayerId || !gameState) return;
    if (gameState.current_turn !== myPlayerId) return;
    ws.send(JSON.stringify({
        type: "action",
        action_type: "attack",
        target_id: targetId,
    }));
}

function handleCellClick(x, y) {
    if (!gameState || gameState.current_turn !== myPlayerId) return;
    const me = gameState.players[myPlayerId];
    if (!me) return;

    const occupantId = gameState.grid[y][x];
    if (occupantId && occupantId !== myPlayerId) {
        const dx = Math.abs(me.position[0] - x);
        const dy = Math.abs(me.position[1] - y);
        if (dx + dy === 1) {
            sendAttack(occupantId);
        }
    }
}

function appendEventFromServer(event) {
    const data = event.data;
    const getName = (pid) => {
        if (!gameState || !gameState.players[pid]) return pid;
        return gameState.players[pid].name;
    };

    switch (event.event_type) {
        case "player_joined":
            appendEvent("join", getName(data.player_id) + " joined the game");
            break;
        case "player_moved":
            appendEvent("move", getName(data.player_id) + " moved to (" + data.to[0] + "," + data.to[1] + ")");
            break;
        case "player_attacked":
            appendEvent("attack", getName(data.attacker_id) + " attacks " + getName(data.target_id) + " for " + data.damage + " damage");
            break;
        case "player_damaged":
            appendEvent("damage", getName(data.player_id) + " has " + data.hp_remaining + " HP left");
            break;
        case "player_died":
            appendEvent("death", getName(data.player_id) + " was slain by " + getName(data.killer_id) + "!");
            break;
        case "turn_started":
            appendEvent("turn", getName(data.player_id) + "'s turn");
            break;
        case "player_left":
            appendEvent("join", getName(data.player_id) + " left the game");
            break;
        case "game_over":
            appendEvent("gameover", data.winner_name + " wins the game!");
            break;
    }
}

function appendEvent(type, message) {
    const log = document.getElementById("event-log");
    const div = document.createElement("div");
    div.className = "event-" + type;
    div.textContent = message;
    log.appendChild(div);
    log.scrollTop = log.scrollHeight;
}

document.addEventListener("keydown", (e) => {
    switch (e.key) {
        case "ArrowUp":    case "w": case "W": sendMove(0, -1); break;
        case "ArrowDown":  case "s": case "S": sendMove(0, 1);  break;
        case "ArrowLeft":  case "a": case "A": sendMove(-1, 0); break;
        case "ArrowRight": case "d": case "D": sendMove(1, 0);  break;
    }
});

document.getElementById("player-name").addEventListener("keydown", (e) => {
    if (e.key === "Enter") joinGame();
});

connect();
