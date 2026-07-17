import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import { GameProvider } from "./store/gameStore";
import "./styles/theme.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <GameProvider>
      <App />
    </GameProvider>
  </StrictMode>,
);
