import { useMemo } from "react";
import { getCurrentWindow } from "@tauri-apps/api/window";
import { isTauri } from "./lib/api";
import { detectLocale, makeT } from "./lib/i18n";
import { MainView } from "./components/MainView";
import { OverlayView } from "./components/OverlayView";
import { AssistantView } from "./components/AssistantView";

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
  const label = windowLabel();
  if (label === "overlay") return <OverlayView t={t} />;
  if (label === "assistant") return <AssistantView t={t} />;
  return <MainView t={t} />;
}
