/**
 * The front door. Against the real server this is the full M8 flow —
 * username + password over HTTP, then a token join over the socket.
 * In mock mode (?mock) any name still opens the door.
 */
import { useState } from "react";
import { useGame, useGameApi, USE_MOCK } from "../store/gameStore";

export function LoginScreen() {
  const api = useGameApi();
  const { loginError } = useGame();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [hint, setHint] = useState("");
  const [busy, setBusy] = useState(false);

  const submit = async (kind: "login" | "register") => {
    const name = username.trim();
    if (!name) {
      setHint("Every traveler has a name. What's yours?");
      return;
    }
    if (!USE_MOCK) {
      if (name.length < 3) {
        setHint("A name needs at least 3 characters.");
        return;
      }
      if (password.length < 6) {
        setHint("Your secret word needs at least 6 characters.");
        return;
      }
    }
    setBusy(true);
    const error =
      kind === "login" ? await api.login(name, password) : await api.register(name, password);
    setBusy(false);
    if (error) setHint(error);
  };

  const defaultHint = USE_MOCK
    ? "Mockup build — any name opens the door."
    : "New here? Sign the ledger to make an account.";

  return (
    <div className="login-screen">
      <div className="login-card">
        <div className="login-flame">🕯️</div>
        <h1 className="login-title">Emberhollow</h1>
        <p className="login-tagline">Come in from the cold.</p>

        <input
          value={username}
          maxLength={20}
          placeholder="Your name"
          autoFocus
          onChange={(e) => setUsername(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && submit("login")}
        />
        <input
          type="password"
          value={password}
          placeholder="A word only you know"
          onChange={(e) => setPassword(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && submit("login")}
        />

        <div className="login-buttons">
          <button className="btn-primary" disabled={busy} onClick={() => submit("login")}>
            {busy ? "Crossing the threshold…" : "Step inside"}
          </button>
          <button className="btn-secondary" disabled={busy} onClick={() => submit("register")}>
            Sign the ledger
          </button>
        </div>
        <p className="login-hint">{hint || loginError || defaultHint}</p>
      </div>
    </div>
  );
}
