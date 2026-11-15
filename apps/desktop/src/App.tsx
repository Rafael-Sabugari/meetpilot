import { useMemo } from "react";
import { getCurrentWindow } from "@tauri-apps/api/window";
import { isTauri } from "./lib/api";
import { detectLocale, makeT } from "./lib/i18n";
import { MainView } from "./components/MainView";
import { OverlayView } from "./components/OverlayView";

function windowLabel(): string {
  if (!isTauri()) return "main";
  try {
    return getCurrentWindow().label;
  } catch {
    return "main";
  }
}

export default function App() {
  const t = useMemo(() => makeT(detectLocale(navigator.language)), []);
  return windowLabel() === "overlay" ? <OverlayView t={t} /> : <MainView t={t} />;
}
