let ws = null;
let myPlayerId = null;
let gameState = null;
let actionLocked = false;
let waitingFor = [];
let bombArmed = false;
let renderedGridWidth = 0;
let renderedGridHeight = 0;
let inspectedObject = null;

const PLAYER_COLORS = ["#4ecca3", "#7ec8e3", "#ffd700", "#c490e4"];
const PLAYER_ICONS = ["⚔", "♠", "♦", "♣"];
const STATS_ICONS = { defense: "🛡"}
const ENEMY_COLOR = "#ff6b35";
const ENEMY_ICONS = {
    default: "👹",
    Goblin: "G",
    Skeleton: "S",
    Rat: "R",
};
const OBJECT_ICONS = {
    chest: "C",
    fire_barrel: "B",
};

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
            case "room_changed":
                handleRoomChanged(msg);
                break;
            case "action_locked":
                handleActionLocked();
                break;
            case "waiting_for":
                handleWaitingFor(msg);
                break;
            case "object_inspection":
                handleObjectInspection(msg);
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
    actionLocked = false;
    document.getElementById("join-screen").style.display = "none";
    document.getElementById("game-screen").style.display = "block";
    initGrid();
    renderAll();
    appendEvent("join", "You joined as " + msg.player_id);
}

function handleStateUpdate(msg) {
    gameState = msg.state;
    actionLocked = false;
    bombArmed = false;
    waitingFor = [];
    if (inspectedObject && !getObjectById(inspectedObject.id)) {
        inspectedObject = null;
    }
    renderAll();
    if (msg.events) {
        msg.events.forEach((e) => appendEventFromServer(e));
    }
}

function handleRoomChanged(msg) {
    gameState = msg.state;
    actionLocked = false;
    bombArmed = false;
    waitingFor = [];
    // Object ids are per-room ("object_1" exists in most rooms) — an id-based
    // staleness check would keep showing the OLD room's object, so always clear.
    inspectedObject = null;
    renderAll();
    appendEvent("join", "You enter " + ((msg.state.room && msg.state.room.name) || "a new room"));
    if (msg.events) {
        msg.events.forEach((e) => appendEventFromServer(e));
    }
}

function handleActionLocked() {
    actionLocked = true;
    bombArmed = false;
    renderAll();
}

function handleWaitingFor(msg) {
    waitingFor = msg.player_ids || [];
    renderPhaseBanner();
}

function handleObjectInspection(msg) {
    inspectedObject = msg.object || null;
    renderInspection();
}

function initGrid() {
    const container = document.getElementById("grid-container");
    const size = getRoomSize();
    renderedGridWidth = size.width;
    renderedGridHeight = size.height;

    container.innerHTML = "";
    container.style.gridTemplateColumns = `repeat(${size.width}, var(--cell-size))`;
    container.style.gridTemplateRows = `repeat(${size.height}, var(--cell-size))`;

    for (let y = 0; y < size.height; y++) {
        for (let x = 0; x < size.width; x++) {
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
    ensureGrid();
    renderRoomInfo();
    renderGrid();
    renderEntities();
    renderInspection();
    renderPhaseBanner();
    updateControls();
}

function getRoomSize() {
    const room = gameState && gameState.room ? gameState.room : {};
    const grid = gameState && Array.isArray(gameState.grid) ? gameState.grid : [];
    const width = gameState?.width || room.width || (grid[0] ? grid[0].length : 10);
    const height = gameState?.height || room.height || grid.length || 10;
    return { width, height };
}

function ensureGrid() {
    const size = getRoomSize();
    const cellCount = document.querySelectorAll(".cell").length;
    if (
        size.width !== renderedGridWidth ||
        size.height !== renderedGridHeight ||
        cellCount !== size.width * size.height
    ) {
        initGrid();
    }
}

function getGridOccupant(x, y) {
    if (!gameState || !Array.isArray(gameState.grid)) return null;
    const row = gameState.grid[y];
    return row ? row[x] : null;
}

function getObjectById(objectId) {
    const objects = gameState && Array.isArray(gameState.objects) ? gameState.objects : [];
    return objects.find((obj) => obj.id === objectId) || null;
}

function getObjectAt(x, y) {
    const objects = gameState && Array.isArray(gameState.objects) ? gameState.objects : [];
    return objects.find((obj) => obj.position[0] === x && obj.position[1] === y) || null;
}

function getObjectIndex() {
    const index = {};
    const objects = gameState && Array.isArray(gameState.objects) ? gameState.objects : [];
    objects.forEach((obj) => {
        index[obj.position[0] + "," + obj.position[1]] = obj;
    });
    return index;
}

function renderGrid() {
    const cells = document.querySelectorAll(".cell");
    const wallSet = new Set(gameState.walls.map((w) => w[0] + "," + w[1]));
    const playerIndex = getPlayerIndex();
    const objectIndex = getObjectIndex();

    cells.forEach((cell) => {
        const x = parseInt(cell.dataset.x);
        const y = parseInt(cell.dataset.y);
        const key = x + "," + y;

        cell.className = "cell";
        cell.innerHTML = "";
        cell.title = "";
        cell.style.color = "";

        if (wallSet.has(key)) {
            cell.classList.add("cell-wall");
            cell.textContent = "█";
            cell.style.color = "#333";
            return;
        }

        const object = objectIndex[key];
        const occupantId = getGridOccupant(x, y);

        if (object) {
            cell.classList.add("cell-object");
            cell.title = object.label;
            renderObject(cell, object, Boolean(occupantId));
        }

        if (!occupantId) return;

        const player = gameState.players[occupantId];
        const enemy = gameState.enemies ? gameState.enemies[occupantId] : null;

        if (player) {
            const idx = playerIndex[occupantId] || 0;
            const color = PLAYER_COLORS[idx % PLAYER_COLORS.length];
            const icon = PLAYER_ICONS[idx % PLAYER_ICONS.length];

            cell.classList.add("cell-player");
            if (occupantId === myPlayerId) cell.classList.add("cell-me");

            const iconEl = document.createElement("div");
            iconEl.className = "entity-icon";
            iconEl.textContent = icon;
            iconEl.style.color = color;
            cell.appendChild(iconEl);

            addHpBar(cell, player.hp, player.max_hp);
        } else if (enemy) {
            cell.classList.add("cell-enemy");
            const icon = ENEMY_ICONS[enemy.name] || ENEMY_ICONS.default;

            const iconEl = document.createElement("div");
            iconEl.className = "entity-icon";
            iconEl.textContent = icon;
            iconEl.style.color = ENEMY_COLOR;
            cell.appendChild(iconEl);

            const nameEl = document.createElement("div");
            nameEl.className = "entity-name";
            nameEl.textContent = enemy.name;
            nameEl.style.color = ENEMY_COLOR;
            cell.appendChild(nameEl);

            addHpBar(cell, enemy.hp, enemy.max_hp);
        }
    });
}

function renderObject(cell, object, hasOccupant) {
    const marker = document.createElement("div");
    marker.className = hasOccupant ? "object-marker object-marker-corner" : "object-marker";
    marker.textContent = OBJECT_ICONS[object.type] || "?";
    cell.appendChild(marker);

    if (!hasOccupant) {
        const nameEl = document.createElement("div");
        nameEl.className = "object-name";
        nameEl.textContent = object.label;
        cell.appendChild(nameEl);
    }
}

function addHpBar(cell, hp, maxHp) {
    const hpBar = document.createElement("div");
    hpBar.className = "hp-bar";
    const hpFill = document.createElement("div");
    hpFill.className = "hp-fill";
    const pct = (hp / maxHp) * 100;
    hpFill.style.width = pct + "%";
    hpFill.style.background = pct > 50 ? "#4ecca3" : pct > 25 ? "#ff8c42" : "#e94560";
    hpBar.appendChild(hpFill);
    cell.appendChild(hpBar);
}

function getPlayerIndex() {
    const index = {};
    const ids = Object.keys(gameState.players).sort();
    ids.forEach((id, i) => { index[id] = i; });
    return index;
}

function renderRoomInfo() {
    const panel = document.getElementById("room-info");
    if (!panel) return;

    const room = gameState.room || {};
    const size = getRoomSize();
    const objects = Array.isArray(gameState.objects) ? gameState.objects : [];
    const rows = [
        ["Name", room.name || gameState.room_name || "Unknown"],
        ["Size", size.width + " x " + size.height],
        ["Mode", room.mode || gameState.phase || "unknown"],
        ["Objects", String(objects.length)],
    ];

    panel.innerHTML = "";
    rows.forEach(([label, value]) => {
        const row = document.createElement("div");
        row.className = "room-row";

        const labelEl = document.createElement("span");
        labelEl.textContent = label;
        row.appendChild(labelEl);

        const valueEl = document.createElement("strong");
        valueEl.textContent = value;
        row.appendChild(valueEl);

        panel.appendChild(row);
    });
}

function renderInspection() {
    const panel = document.getElementById("inspection-panel");
    const title = document.getElementById("inspection-title");
    const description = document.getElementById("inspection-description");
    const details = document.getElementById("inspection-details");
    if (!panel || !title || !description || !details) return;

    if (!inspectedObject) {
        panel.hidden = true;
        return;
    }

    panel.hidden = false;
    title.textContent = inspectedObject.label || "Object";
    description.textContent = inspectedObject.description || "";
    details.innerHTML = "";

    (inspectedObject.details || []).forEach((detail) => {
        const li = document.createElement("li");
        li.textContent = detail;
        details.appendChild(li);
    });
}

function renderEntities() {
    const list = document.getElementById("entities-list");
    list.innerHTML = "";
    const playerIndex = getPlayerIndex();

    Object.values(gameState.players).forEach((p) => {
        const li = document.createElement("li");
        const idx = playerIndex[p.id] || 0;
        const color = PLAYER_COLORS[idx % PLAYER_COLORS.length];
        const icon = PLAYER_ICONS[idx % PLAYER_ICONS.length];
        const isMe = p.id === myPlayerId;

        li.innerHTML = `
            <span style="color:${color}">${icon} ${p.name}${isMe ? " (you)" : ""}</span>
            <span>${p.hp}/${p.max_hp} HP</span>
            <span>${p.defense} ${STATS_ICONS.defense}</span>
        `;
        if (!p.is_alive) li.classList.add("entity-dead");
        list.appendChild(li);
    });

    if (gameState.enemies) {
        Object.values(gameState.enemies).forEach((e) => {
            const li = document.createElement("li");
            const icon = ENEMY_ICONS[e.name] || ENEMY_ICONS.default;
            li.innerHTML = `
                <span style="color:${ENEMY_COLOR}">${icon} ${e.name}</span>
                <span>${e.hp}/${e.max_hp} HP</span>
            `;
            if (!e.is_alive) li.classList.add("entity-dead");
            list.appendChild(li);
        });
    }
}

function renderPhaseBanner() {
    const banner = document.getElementById("phase-banner");

    if (!gameState.started) {
        banner.textContent = "Waiting for players...";
        banner.className = "phase-waiting";
        return;
    }

    const gameOver = checkGameOver();
    if (gameOver) {
        banner.textContent = gameOver;
        banner.className = "phase-gameover";
        return;
    }

    if (bombArmed && !actionLocked) {
        banner.textContent = "💣 Bomb armed — click a target tile. Blast hits EVERYONE in radius, including you! (B or Esc to cancel)";
        banner.className = "phase-action";
        return;
    }

    if (actionLocked) {
        if (waitingFor.length > 0) {
            const names = waitingFor.map((pid) => getName(pid));
            banner.textContent = "Action locked — waiting for: " + names.join(", ");
        } else {
            banner.textContent = "Action locked — waiting for others...";
        }
        banner.className = "phase-locked";
        return;
    }

    banner.textContent = "Round " + (gameState.round + 1) + " — Choose your action!";
    banner.className = "phase-action";
}

function updateControls() {
    const controls = document.getElementById("controls");
    if (actionLocked || !gameState.started) {
        controls.classList.add("locked");
    } else {
        controls.classList.remove("locked");
    }
}

function checkGameOver() {
    const livingPlayers = Object.values(gameState.players).filter((p) => p.is_alive);
    const livingEnemies = gameState.enemies ? Object.values(gameState.enemies).filter((e) => e.is_alive) : [];
    const totalEnemies = gameState.enemies ? Object.keys(gameState.enemies).length : 0;

    if (livingPlayers.length === 0 && Object.keys(gameState.players).length > 0) {
        return "Defeat — the dungeon claims all...";
    }
    if (totalEnemies > 0 && livingEnemies.length === 0 && livingPlayers.length > 0) {
        return "Victory — all enemies defeated!";
    }
    return null;
}

function sendMove(dx, dy) {
    if (!ws || !myPlayerId || !gameState || actionLocked) return;
    if (!gameState.started) return;
    ws.send(JSON.stringify({
        type: "action",
        action_type: "move",
        direction: [dx, dy],
    }));
}

function sendAttack(targetId) {
    if (!ws || !myPlayerId || !gameState || actionLocked) return;
    if (!gameState.started) return;
    ws.send(JSON.stringify({
        type: "action",
        action_type: "attack",
        target_id: targetId,
    }));
}

function sendWait() {
    if (!ws || !myPlayerId || !gameState || actionLocked) return;
    if (!gameState.started) return;
    ws.send(JSON.stringify({
        type: "action",
        action_type: "wait",
    }));
}

function sendBomb(x, y) {
    if (!ws || !myPlayerId || !gameState || actionLocked || !gameState.started) return;
    ws.send(JSON.stringify({
        type: "action",
        action_type: "bomb",
        target_tile: [x, y],
    }));
}

function sendInspectObject(objectId) {
    if (!ws || !myPlayerId || !gameState) return;
    ws.send(JSON.stringify({
        type: "inspect_object",
        object_id: objectId,
    }));
}

function toggleBomb() {
    if (actionLocked || !gameState || !gameState.started) return;
    bombArmed = !bombArmed;
    renderPhaseBanner();
}

function handleCellClick(x, y) {
    if (!gameState) return;

    if (bombArmed) {
        if (actionLocked || !gameState.started) return;
        sendBomb(x, y);
        bombArmed = false;
        return;
    }

    const object = getObjectAt(x, y);
    if (object) {
        inspectedObject = object;
        renderInspection();
        sendInspectObject(object.id);
        return;
    }

    if (actionLocked || !gameState.started) return;
    const me = gameState.players[myPlayerId];
    if (!me || !me.is_alive) return;

    const occupantId = getGridOccupant(x, y);
    if (occupantId && occupantId !== myPlayerId) {
        const dx = Math.abs(me.position[0] - x);
        const dy = Math.abs(me.position[1] - y);
        if (dx + dy === 1) {
            sendAttack(occupantId);
        }
    }
}

function getName(id) {
    if (!gameState) return id;
    if (gameState.players[id]) return gameState.players[id].name;
    if (gameState.enemies && gameState.enemies[id]) return gameState.enemies[id].name;
    return id;
}

function appendEventFromServer(event) {
    const data = event.data;

    switch (event.event_type) {
        case "player_joined":
            appendEvent("join", getName(data.player_id) + " joined the game");
            break;
        case "player_moved":
            appendEvent("move", getName(data.player_id) + " moved to (" + data.to[0] + "," + data.to[1] + ")");
            break;
        case "player_attacked":
            appendEvent("attack", getName(data.attacker_id) + " attacks " + getName(data.target_id) + " for " + data.damage + " dmg");
            break;
        case "player_damaged":
            appendEvent("damage", getName(data.player_id) + " has " + data.hp_remaining + " HP left");
            break;
        case "player_died":
            appendEvent("death", getName(data.player_id) + " was slain by " + getName(data.killer_id) + "!");
            break;
        case "enemy_moved":
            appendEvent("enemy", data.name + " moves to (" + data.to[0] + "," + data.to[1] + ")");
            break;
        case "enemy_attacked":
            appendEvent("enemy", data.attacker_name + " attacks " + getName(data.target_id) + " for " + data.damage + " dmg");
            break;
        case "enemy_died":
            appendEvent("death", getName(data.player_id) + " was slain!");
            break;
        case "bomb_thrown":
            appendEvent("attack", getName(data.player_id) + " hurls a bomb at (" + data.tile[0] + "," + data.tile[1] + ")");
            break;
        case "round_started":
            appendEvent("round", "— Round " + (data.round + 1) + " —");
            break;
        case "player_left":
            appendEvent("join", (data.name || getName(data.player_id)) + " left the game");
            break;
        case "player_entered_door":
            appendEvent("move", (data.name || getName(data.player_id)) + " slips through the door");
            break;
        case "game_over":
            appendEvent("gameover", data.winner_name);
            break;
        case "invalid_action":
            appendEvent("error", "Action rejected: " + (data.reason || "invalid action"));
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
    if (document.activeElement && document.activeElement.tagName === "INPUT") return;

    let handled = true;
    switch (e.key) {
        case "ArrowUp":    case "w": case "W": sendMove(0, -1); break;
        case "ArrowDown":  case "s": case "S": sendMove(0, 1);  break;
        case "ArrowLeft":  case "a": case "A": sendMove(-1, 0); break;
        case "ArrowRight": case "d": case "D": sendMove(1, 0);  break;
        case " ": sendWait(); break;
        case "b": case "B": toggleBomb(); break;
        case "Escape": bombArmed = false; renderPhaseBanner(); break;
        default: handled = false;
    }
    if (handled) e.preventDefault();
});

document.getElementById("player-name").addEventListener("keydown", (e) => {
    if (e.key === "Enter") joinGame();
});

connect();
