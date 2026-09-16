import {
  Bike,
  Bus,
  Camera,
  Car,
  Check,
  Eraser,
  Info,
  PersonStanding,
  Plane,
  Search,
  Shapes,
  Target,
  Train,
  Truck
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { useModelClasses } from "./jobsApi";
function classIcon(name) {
  const key = name.toLowerCase();
  if (key.includes("person") || key.includes("pedestrian")) return PersonStanding;
  if (key.includes("bicyc") || key.includes("motor")) return Bike;
  if (key.includes("car")) return Car;
  if (key.includes("airplan") || key.includes("plane")) return Plane;
  if (key.includes("bus")) return Bus;
  if (key.includes("truck")) return Truck;
  if (key.includes("train")) return Train;
  return Shapes;
}
function ObjectClassesEditor({
  modelId,
  objectClasses,
  onChange
}) {
  const classes = useModelClasses(modelId);
  const [search, setSearch] = useState("");
  const [detectAll, setDetectAll] = useState(true);
  const [snapshotAll, setSnapshotAll] = useState(true);
  const available = useMemo(() => {
    const names = Object.values(classes.data?.data.object_names ?? {});
    return names.length ? [...new Set(names)] : [];
  }, [classes.data]);
  useEffect(() => {
    if (available.length === 0) return;
    const existing = new Map(objectClasses.map((oc) => [oc.class_name, oc]));
    onChange(
      available.map((name) => ({
        class_name: name,
        detection: existing.get(name)?.detection ?? detectAll,
        snapshot: existing.get(name)?.snapshot ?? snapshotAll
      }))
    );
  }, [available.length, modelId]);
  const filtered = available.filter((name) => name.toLowerCase().includes(search.toLowerCase()));
  const setField = (className, field, value) => onChange(
    objectClasses.map((oc) => {
      if (oc.class_name !== className) return oc;
      if (field === "snapshot") {
        const newSnapshot = value;
        return { ...oc, snapshot: newSnapshot, detection: newSnapshot ? true : oc.detection };
      }
      const newDetection = value;
      return { ...oc, detection: newDetection, snapshot: newDetection ? oc.snapshot : false };
    })
  );
  const toggleColumn = (field) => {
    if (field === "detection") {
      const next = !detectAll;
      setDetectAll(next);
      onChange(objectClasses.map((oc) => ({ ...oc, detection: next, snapshot: next ? oc.snapshot : false })));
    } else {
      const next = !snapshotAll;
      setSnapshotAll(next);
      onChange(objectClasses.map((oc) => ({ ...oc, snapshot: next, detection: next ? true : oc.detection })));
    }
  };
  return <div className="oc-editor">
      <div className="oc-editor__head">
        <div className="oc-editor__title-row">
          <span className="oc-editor__title">Object Classes</span>
          <span className="oc-editor__info" title="Classes this model can detect">
            <Info size={13} aria-hidden />
          </span>
        </div>
        <p className="oc-editor__sub">Enable Detection to unlock Snapshot and ReID</p>
      </div>

      <div className="oc-editor__actions">
        <button
    type="button"
    className="oc-editor__action-btn"
    onClick={() => {
      setDetectAll(true);
      setSnapshotAll(true);
      onChange(objectClasses.map((oc) => ({ ...oc, detection: true, snapshot: true })));
    }}
  >
          <Check size={14} aria-hidden />
          Select All
        </button>
        <button
    type="button"
    className="oc-editor__action-btn"
    onClick={() => {
      setDetectAll(false);
      setSnapshotAll(false);
      onChange(objectClasses.map((oc) => ({ ...oc, detection: false, snapshot: false })));
    }}
  >
          <Eraser size={14} aria-hidden />
          Clear All
        </button>
      </div>

      <div className="oc-editor__search">
        <Search size={14} aria-hidden />
        <input
    className="oc-editor__search-input"
    placeholder="Search classes..."
    value={search}
    onChange={(e) => setSearch(e.target.value)}
  />
      </div>

      <div className="oc-editor__chips">
        <button
    type="button"
    className={`oc-chip oc-chip--detect${detectAll ? " is-active" : ""}`}
    onClick={() => toggleColumn("detection")}
    aria-pressed={detectAll}
  >
          <Target size={13} aria-hidden />
          Detection
          {detectAll && <Check size={12} aria-hidden />}
        </button>
        <button
    type="button"
    className={`oc-chip oc-chip--snapshot${snapshotAll ? " is-active" : ""}`}
    onClick={() => toggleColumn("snapshot")}
    aria-pressed={snapshotAll}
  >
          <Camera size={13} aria-hidden />
          Snapshot
          {snapshotAll && <Check size={12} aria-hidden />}
        </button>
      </div>

      <div className="oc-editor__list">
        {filtered.length === 0 && <p className="oc-editor__empty">No classes found for this model.</p>}
        {filtered.map((name) => {
    const Icon = classIcon(name);
    const oc = objectClasses.find((o) => o.class_name === name);
    return <div key={name} className="oc-class-row">
              <span className="oc-class-row__icon">
                <Icon size={15} aria-hidden />
              </span>
              <span className="oc-class-row__name">{name}</span>
              <div className="oc-class-row__toggles">
                <button
      type="button"
      className={`oc-toggle oc-toggle--detect${oc?.detection ? " is-active" : ""}`}
      onClick={() => setField(name, "detection", !oc?.detection)}
      aria-label={`Detection for ${name}`}
    >
                  <Target size={14} aria-hidden />
                  {oc?.detection && <span className="oc-toggle__check">
                      <Check size={9} strokeWidth={3} aria-hidden />
                    </span>}
                </button>
                <button
      type="button"
      className={`oc-toggle oc-toggle--snapshot${oc?.snapshot ? " is-active" : ""}`}
      onClick={() => setField(name, "snapshot", !oc?.snapshot)}
      aria-label={`Snapshot for ${name}`}
    >
                  <Camera size={14} aria-hidden />
                  {oc?.snapshot && <span className="oc-toggle__check">
                      <Check size={9} strokeWidth={3} aria-hidden />
                    </span>}
                </button>
              </div>
            </div>;
  })}
      </div>
    </div>;
}
export {
  ObjectClassesEditor
};
