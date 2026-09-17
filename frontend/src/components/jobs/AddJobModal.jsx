import {
  Layers,
  Send,
  X
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { inferStreamType, useFootage, useKpiCatalog, useRegisterJob, useTrackers } from "./jobsApi";
import { buildRegisterRequest, emptyDraft } from "./draft";
import { validateSourceUrl } from "./validation";
import { CameraMarkingModal } from "./CameraMarkingModal";
import { ObjectClassesEditor } from "./ObjectClassesEditor";
const KPI_REQUIRES_REGIONS = /* @__PURE__ */ new Set(["line_passing", "roi_region"]);
/** Backend `_resolve_source` vocabulary: rtsp | http | file | webcam. */
function inferUrlType(url) {
  return inferStreamType(url);
}
function SectionPanel({
  title,
  subtitle,
  children
}) {
  return <section className="ajm-section">
      <header className="ajm-section__head">
        <h3 className="ajm-section__title">{title}</h3>
        {subtitle && <p className="ajm-section__sub">{subtitle}</p>}
      </header>
      <div className="ajm-section__body">{children}</div>
    </section>;
}
function UseCaseSection({
  draft,
  patch,
  entries,
  loading
}) {
  return <SectionPanel
    title="Use Case"
  >
      <label className="field">
        <span className="field__label">Activity Type</span>
        {loading ? <span className="ajm-loading">Loading the list of measurements…</span> : <select
    className="input select"
    value={draft.kpi_name}
    onChange={(e) => patch({ kpi_name: e.target.value, model_infos: [], regions: [] })}
  >
            <option value="">Select a use case…</option>
            {entries.map((entry) => <option key={entry.kpi_name} value={entry.kpi_name}>
                {entry.kpi_name}
              </option>)}
          </select>}
      </label>
    </SectionPanel>;
}
function ModelSection({
  draft,
  patch,
  kpi,
  trackerNames
}) {
  if (!kpi) {
    return <SectionPanel title="Model pipeline">
        <p className="ajm-empty">Choose a Use Case above to see the available AI models.</p>
      </SectionPanel>;
  }
  const stageModels = (stage) => kpi.models.filter((m) => m.stage === stage);
  const stage0Models = stageModels(0);
  const stageNModels = stageModels(1);
  const pickedForStage = (models) => draft.model_infos.find((m) => models.some((sm) => sm.model_id === m.model_id));
  const selectModel = (stage, modelId) => {
    if (!modelId) {
      patch({
        model_infos: draft.model_infos.filter(
          (m) => !kpi.models.some((km) => km.stage === stage && km.model_id === m.model_id)
        )
      });
      return;
    }
    const stage0Picked = draft.model_infos.find(
      (m) => stage0Models.some((sm) => sm.model_id === m.model_id)
    );
    const otherPicked = draft.model_infos.filter(
      (m) => m.model_id !== (pickedForStage(stageModels(stage))?.model_id ?? "")
    );
    patch({
      model_infos: [
        ...otherPicked,
        {
          model_id: modelId,
          order: otherPicked.length,
          lead_by: stage > 0 ? stage0Picked?.model_id ?? null : null,
          track_name: stage === 0 ? trackerNames[0] ?? null : null
        }
      ],
      // A model promoted to a stage must not also run as an extra.
      extra_models: (draft.extra_models ?? []).filter((m) => m !== modelId)
    });
  };
  const setInfo = (modelId, p) => patch({ model_infos: draft.model_infos.map((m) => m.model_id === modelId ? { ...m, ...p } : m) });
  // Every installed model except the ones already chosen as a pipeline stage —
  // running the same weights twice on a frame would only cost time.
  const chosenIds = new Set(draft.model_infos.map((m) => m.model_id));
  const availableExtras = [...new Set(kpi.models.map((m) => m.model_id))]
    .filter((id) => !chosenIds.has(id))
    .sort();
  const renderStagePanel = (label, icon, models, stage) => {
    const Icon = icon;
    const picked = pickedForStage(models);
    return <div className="stage-panel ajm-stage">
        <div className="stage-panel__head">
          <span className="field__label row" style={{ gap: 8 }}>
            <Icon size={14} aria-hidden /> {label}
          </span>
        </div>
        <div className="ajm-stage__picks">
          <label className="ajm-stage__pick">
            <span className="ajm-stage__pick-label">Model</span>
            <select
      className="select"
      value={picked?.model_id ?? ""}
      onChange={(e) => selectModel(stage, e.target.value)}
      aria-label={`Model for ${label}`}
    >
              <option value="">Select a model…</option>
              {models.map((m) => <option key={m.model_id} value={m.model_id}>
                  {m.model_id}
                </option>)}
            </select>
          </label>
          {picked && stage === 0 && <label className="ajm-stage__pick">
              <span className="ajm-stage__pick-label">Tracker</span>
              <select
      className="select"
      value={picked.track_name ?? ""}
      onChange={(e) => setInfo(picked.model_id, { track_name: e.target.value || null })}
      aria-label={`Tracker for ${picked.model_id}`}
    >
                <option value="">no tracker</option>
                {trackerNames.map((t) => <option key={t} value={t}>{t}</option>)}
              </select>
            </label>}
          {picked && stage > 0 && <label className="ajm-stage__pick">
              <span className="ajm-stage__pick-label">Runs after</span>
              <select
      className="select"
      value={picked.lead_by ?? ""}
      onChange={(e) => setInfo(picked.model_id, { lead_by: e.target.value || null })}
      aria-label={`Stage 0 lead for ${picked.model_id}`}
    >
                <option value="">Select a stage 0 model…</option>
                {draft.model_infos.filter((m) => stage0Models.some((sm) => sm.model_id === m.model_id)).map((m) => <option key={m.model_id} value={m.model_id}>{m.model_id}</option>)}
              </select>
            </label>}
        </div>
      </div>;
  };
  return <SectionPanel title="Model Pipeline">
      <div className="ajm-stages">
        {renderStagePanel("Stage 0", Layers, stage0Models, 0)}
        {stageNModels.length > 0 && renderStagePanel("Stage 1", Layers, stageNModels, 1)}
      </div>

      <div className="ajm-extras">
        <span className="field__label">Also run on the same frame</span>
        <p className="ajm-extras__hint">
          These detect alongside the model above — a fire model and a product
          model can watch the same feed. Extra models are not tracked and do not
          feed the people analytics, so counting stays on the model above.
        </p>
        <div className="ajm-extras__list">
          {availableExtras.length === 0 ? (
            <span className="ajm-empty">No other models installed.</span>
          ) : availableExtras.map((m) => {
            const on = (draft.extra_models ?? []).includes(m);
            return (
              <label key={m} className={`ajm-extra${on ? " is-on" : ""}`}>
                <input
                  type="checkbox"
                  checked={on}
                  onChange={(e) => patch({
                    extra_models: e.target.checked
                      ? [...(draft.extra_models ?? []), m]
                      : (draft.extra_models ?? []).filter((x) => x !== m),
                  })}
                />
                <span className="mono">{m}</span>
              </label>
            );
          })}
        </div>
      </div>

      {draft.model_infos.map((mi) => <div key={mi.model_id} className="ajm-model-oc">
          <div className="ajm-model-oc__label">
            <span className="mono" style={{ fontSize: "var(--fs-sm)" }}>{mi.model_id}</span>
            <span className="ajm-model-oc__count">
              {(mi.object_classes ?? []).filter((oc) => oc.detection).length} classes
            </span>
          </div>
          <ObjectClassesEditor
    modelId={mi.model_id}
    objectClasses={mi.object_classes ?? []}
    onChange={(oc) => setInfo(mi.model_id, { object_classes: oc })}
  />
        </div>)}
    </SectionPanel>;
}
const SOURCE_PLACEHOLDER = {
  rtsp: "rtsp://user:pass@192.168.1.10:554/stream",
  http: "https://example.com/clip.mp4",
  webcam: "0",
};

const SOURCE_HINT = {
  rtsp: "RTSP address of a live camera.",
  http: "Direct link to a video file.",
  webcam: "Index of a camera attached to the server — usually 0.",
};

function SourceSection({
  draft,
  patch,
  onOpenMarking,
  footage
}) {
  // Uploaded clips are the common case, so default there unless the draft
  // already carries another kind of source (e.g. reopened from a config).
  const [sourceMode, setSourceMode] = useState(
    draft.url && draft.url_type && draft.url_type !== "file" ? draft.url_type : "upload"
  );
  const urlError = draft.url ? validateSourceUrl(draft.url, draft.url_type) : null;
  // Preview is gated on the source alone — any feed can be inspected, whether or
  // not the chosen activity needs regions drawn on it.
  const markingDisabledReason = !draft.url.trim() ? "Enter a source URL first to preview the stream." : null;
  return <SectionPanel
    title="Source"
  >
      <div className="grid grid--2">
        <label className="field">
          <span className="field__label">Camera ID (number)</span>
          <input
    className="input input--mono"
    value={draft.camera_id}
    onChange={(e) => patch({ camera_id: e.target.value })}
    placeholder="1"
  />
        </label>
        <label className="field">
          <span className="field__label">Zone</span>
          <input
    className="input input--mono"
    value={draft.location_id}
    onChange={(e) => patch({ location_id: e.target.value })}
    placeholder="entrance"
  />
        </label>
      </div>
      <label className="field" style={{ marginTop: "var(--sp-3)" }}>
        <span className="field__label">Source type</span>
        <select
    className="input"
    value={sourceMode}
    onChange={(e) => {
      const mode = e.target.value;
      setSourceMode(mode);
      // Switching mode clears the URL: a footage: reference and an RTSP
      // address are never interchangeable, and a stale value would fail
      // validation in a way that points at the wrong field.
      patch({ url: "", url_type: mode === "upload" ? "file" : mode });
    }}
  >
          <option value="upload">Uploaded video (recommended)</option>
          <option value="rtsp">RTSP stream</option>
          <option value="http">Direct video link</option>
          <option value="webcam">Webcam index</option>
        </select>
      </label>

      {sourceMode === "upload" ? (
        <div className="field" style={{ marginTop: "var(--sp-3)" }}>
          <span className="field__label">Stored clip</span>
          <select
      className={`input input--mono${urlError ? " input--invalid" : ""}`}
      value={draft.url}
      onChange={(e) => patch({ url: e.target.value, url_type: "file" })}
      disabled={footage.isLoading && footage.items.length === 0}
    >
            <option value="">
              {footage.isLoading && footage.items.length === 0
                ? "Loading clips…"
                : footage.items.length === 0
                  ? "No clips uploaded yet — upload one below"
                  : "Select an uploaded video…"}
            </option>
            {footage.items.map((f) => (
              <option key={f.filename} value={`footage:${f.filename}`}>
                {f.filename} — feed {f.camera_id}
              </option>
            ))}
          </select>

          <div className="ajm-upload-row">
            <label className={`ajm-upload-btn${footage.uploading ? " is-busy" : ""}`}>
              <input
        type="file"
        accept="video/mp4,video/x-msvideo,video/x-matroska,video/webm,video/quicktime"
        hidden
        disabled={footage.uploading}
        onChange={async (e) => {
          const file = e.target.files?.[0];
          e.target.value = "";
          if (!file) return;
          const stored = await footage.upload(file, draft.camera_id);
          // Select the clip we just uploaded so the user does not have to
          // find it in the list themselves.
          if (stored) patch({ url: `footage:${stored}`, url_type: "file" });
        }}
      />
              {footage.uploading ? "Uploading…" : "Upload video"}
            </label>
            <span className="ajm-hint">MP4, AVI, MKV, WEBM or MOV.</span>
          </div>

          <label className="ajm-loop">
            <input
      type="checkbox"
      checked={Boolean(draft.loop)}
      onChange={(e) => patch({ loop: e.target.checked })}
    />
            <span>
              <strong>Replay on finish</strong>
              <em>
                Restarts the clip instead of stopping at the end. Counts and
                identities keep adding up across laps, so use it to demo or to
                test a short video — not to measure real numbers.
              </em>
            </span>
          </label>

          {footage.error && <span className="field__error">{footage.error}</span>}
          {urlError && <span className="field__error">{urlError}</span>}
        </div>
      ) : (
        <label className="field" style={{ marginTop: "var(--sp-3)" }}>
          <span className="field__label">
            {sourceMode === "webcam" ? "Webcam index" : "Source URL"}
          </span>
          <input
      className={`input input--mono${urlError ? " input--invalid" : ""}`}
      value={draft.url}
      onChange={(e) => patch({ url: e.target.value, url_type: inferUrlType(e.target.value) })}
      placeholder={SOURCE_PLACEHOLDER[sourceMode]}
    />
          {urlError
            ? <span className="field__error">{urlError}</span>
            : <span className="field__hint">{SOURCE_HINT[sourceMode]}</span>}
        </label>
      )}

      <label className="ajm-toggle">
        <input
    type="checkbox"
    checked={draft.enable_reid !== false}
    onChange={(e) => patch({ enable_reid: e.target.checked })}
  />
        <span>
          <strong>Cross-camera Re-ID</strong>
          <em>
            Gives each person a 512-d OSNet embedding and matches them against a
            shared gallery, so the same shopper keeps one identity across feeds.
            It is the heaviest step in the pipeline — turn it off for a single
            camera, or when you only need per-feed tracking.
          </em>
        </span>
      </label>

      <div className="ajm-source-actions">
        <button
    type="button"
    className="ajm-marking-btn"
    onClick={onOpenMarking}
    disabled={!draft.url.trim()}
    title={draft.url.trim() ? "Preview the feed and draw regions" : "Enter a source URL first to preview the stream"}
  >
          Regions & Preview
        </button>
        {markingDisabledReason && <p className="ajm-hint">{markingDisabledReason}</p>}
      </div>
    </SectionPanel>;
}
function AddJobModal({ open, onClose }) {
  const [draft, setDraft] = useState(emptyDraft);
  const [created, setCreated] = useState(null);
  const [markingOpen, setMarkingOpen] = useState(false);
  const catalog = useKpiCatalog();
  const trackers = useTrackers();
  const footage = useFootage();
  const register = useRegisterJob();
  const kpi = useMemo(
    () => catalog.data?.data.find((k) => k.kpi_name === draft.kpi_name),
    [catalog.data, draft.kpi_name]
  );
  const patch = (p) => setDraft((d) => ({ ...d, ...p }));
  useEffect(() => {
    if (open) {
      setDraft(emptyDraft);
      setCreated(null);
      setMarkingOpen(false);
    }
  }, [open]);
  useEffect(() => {
    if (!open) return;
    const onKey = (e) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);
  if (!open) return null;
  const canRegister = Boolean(draft.kpi_name) && draft.model_infos.length > 0 && Boolean(draft.camera_id.trim()) && Boolean(draft.location_id.trim()) && Boolean(draft.url.trim()) && (!draft.kpi_name || !KPI_REQUIRES_REGIONS.has(draft.kpi_name) || draft.regions.length > 0);
  const registerDisabledReason = !draft.kpi_name ? "Select a use case." : draft.model_infos.length === 0 ? "Pick at least one model." : !draft.camera_id.trim() ? "Enter a camera ID." : !draft.location_id.trim() ? "Enter a location ID." : !draft.url.trim() ? "Enter a source URL." : draft.kpi_name && KPI_REQUIRES_REGIONS.has(draft.kpi_name) && draft.regions.length === 0 ? "Draw at least one region for this use case." : null;
  const submit = async () => {
    setCreated(null);
    try {
      const { data } = await register.mutateAsync(buildRegisterRequest(draft, kpi));
      setCreated(data);
      setTimeout(() => onClose(), 1200);
    } catch {
    }
  };
  const sourceProps = { draft, patch, kpi };
  return <div
    className="ajm-backdrop"
    onMouseDown={(e) => {
      if (e.target === e.currentTarget) onClose();
    }}
  >
      <div className="ajm" role="dialog" aria-modal="true" aria-labelledby="ajm-title">
        <header className="ajm__head">
          <div className="ajm__copy">
            <h2 className="ajm__title" id="ajm-title">Add Camera Job</h2>
            <p className="ajm__sub">
              Choose what to watch for, pick the AI models, then connect your camera — done in three short steps.
            </p>
          </div>
          <button type="button" className="ajm__close" onClick={onClose} aria-label="Close">
            <X size={16} />
          </button>
        </header>

        <div className="ajm__body">
          <UseCaseSection {...sourceProps} entries={catalog.data?.data ?? []} loading={catalog.isLoading} />
          <ModelSection {...sourceProps} trackerNames={(trackers.data?.data ?? []).map((t) => t.name)} />
          <SourceSection {...sourceProps} footage={footage} onOpenMarking={() => setMarkingOpen(true)} />
        </div>

        <footer className="ajm__footer">
          {created && <span className="ajm-created">
              Registered <strong className="mono">{created}</strong>
            </span>}
          {register.isError && <span className="ajm-error">Failed to register — {String(register.error)}</span>}
          {!canRegister && registerDisabledReason && <span className="ajm-hint">{registerDisabledReason}</span>}
          <button
    type="button"
    className="ajm-register"
    onClick={() => void submit()}
    disabled={!canRegister || register.isPending}
  >
            {register.isPending ? "Registering\u2026" : <>
                <Send size={14} aria-hidden /> Register job
              </>}
          </button>
        </footer>
      </div>
      <CameraMarkingModal
    open={markingOpen}
    onClose={() => setMarkingOpen(false)}
    draft={draft}
    kpi={kpi}
    regions={draft.regions}
    onChange={(regions) => patch({ regions })}
    onFrameSize={(w, h) => patch({ frame_width: w, frame_height: h })}
  />
    </div>;
}
export {
  AddJobModal
};
