import { dbfsToPercent } from "../lib/indicator";

interface Props {
  label: string;
  db: number | null;
  silentLabel: string;
  active: boolean;
}

export function LevelMeter({ label, db, silentLabel, active }: Props) {
  const pct = db === null ? 0 : dbfsToPercent(db);
  return (
    <div className="meter">
      <div className="meter__head">
        <span>{label}</span>
        <span className="meter__value">
          {active ? (db === null ? silentLabel : `${db.toFixed(1)} dBFS`) : "—"}
        </span>
      </div>
      <div
        className="meter__track"
        role="meter"
        aria-label={label}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={pct}
      >
        <div className="meter__fill" style={{ width: `${pct}%` }} />
      </div>
    </div>
  );
}
