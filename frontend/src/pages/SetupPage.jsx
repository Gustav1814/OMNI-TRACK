import React, { useEffect, useMemo, useState } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import {
    Boxes, Camera, Check, ClipboardList, Link as LinkIcon, PackagePlus,
    Save, ShieldCheck, SlidersHorizontal, Store, Users,
} from 'lucide-react';
import {
    camerasAPI, humanlessAPI, modelAPI, setupAPI, systemAPI,
} from '../services/api';
import useLivePoll from '../hooks/useLivePoll';
import useUserRole from '../hooks/useUserRole';
import ContentCard from '../components/ui/ContentCard';
import StatusTile from '../components/ui/StatusTile';

const money = (value) => `$${Number(value || 0).toFixed(2)}`;

const TABS = [
    { id: 'profile', path: '/setup', label: 'Store profile', icon: Store },
    { id: 'products', path: '/setup/products', label: 'Products', icon: Boxes },
    { id: 'zones', path: '/setup/zones', label: 'Shelf zones', icon: ClipboardList },
    { id: 'cameras', path: '/setup/cameras', label: 'Cameras', icon: Camera },
    { id: 'models', path: '/setup/models', label: 'Models', icon: SlidersHorizontal },
    { id: 'integrations', path: '/setup/integrations', label: 'Integrations', icon: LinkIcon },
    { id: 'users', path: '/setup/users', label: 'Users', icon: Users },
];

const MODULES = [
    'crowd', 'checkout', 'fire', 'vibe', 'reid', 'shelf',
    'peak-hours', 'demographics', 'emotion', 'humanless', 'loss-prevention',
];

function activeTabFromPath(pathname) {
    const tab = TABS.find((item) => item.path === pathname);
    return tab?.id || 'profile';
}

export default function SetupPage() {
    const location = useLocation();
    const navigate = useNavigate();
    const { canAdmin, canOperate, role } = useUserRole();
    const activeTab = activeTabFromPath(location.pathname);
    const wizard = new URLSearchParams(location.search).get('wizard') === '1';

    const [profileForm, setProfileForm] = useState({
        store_name: 'OmniTrack Store',
        deployment_type: 'custom',
        enabled_modules: [],
        zone_templates: [],
    });
    const [productForm, setProductForm] = useState({ sku: '', name: '', category: '', price: '' });
    const [zoneForm, setZoneForm] = useState({
        zone_id: '', name: '', camera_id: '', product_id: '', current_stock: 0, low_stock_threshold: 3,
    });
    const [busy, setBusy] = useState(false);
    const [notice, setNotice] = useState(null);
    const [error, setError] = useState(null);

    const { data: templates } = useLivePoll(() => setupAPI.templates(), { intervalMs: 60000 });
    const { data: profile, refresh: refreshProfile } = useLivePoll(() => setupAPI.profile(), { intervalMs: 15000 });
    const needsProducts = ['products', 'zones'].includes(activeTab);
    const needsZones = activeTab === 'zones';
    const needsCameras = ['zones', 'cameras'].includes(activeTab);
    const needsModels = activeTab === 'models';
    const needsHealth = activeTab === 'integrations';

    const { data: products, refresh: refreshProducts } = useLivePoll(
        () => humanlessAPI.products(false),
        { intervalMs: 12000, enabled: needsProducts }
    );
    const { data: zones, refresh: refreshZones } = useLivePoll(
        () => humanlessAPI.shelfZones(false),
        { intervalMs: 12000, enabled: needsZones }
    );
    const { data: cameras } = useLivePoll(
        () => camerasAPI.list(),
        { intervalMs: 15000, enabled: needsCameras }
    );
    const { data: models } = useLivePoll(
        () => modelAPI.list(),
        { intervalMs: 30000, enabled: needsModels }
    );
    const { data: health } = useLivePoll(
        () => systemAPI.health(),
        { intervalMs: 12000, enabled: needsHealth }
    );

    useEffect(() => {
        if (!profile) return;
        setProfileForm({
            store_name: profile.store_name || 'OmniTrack Store',
            deployment_type: profile.deployment_type || 'custom',
            enabled_modules: profile.enabled_modules || [],
            zone_templates: profile.zone_templates || [],
        });
    }, [profile]);

    const templateRows = Array.isArray(templates) ? templates : [];
    const productRows = Array.isArray(products) ? products : [];
    const zoneRows = Array.isArray(zones) ? zones : [];
    const cameraRows = Array.isArray(cameras) ? cameras : [];
    const modelRows = Array.isArray(models?.models) ? models.models : [];
    const activeTemplate = templateRows.find((t) => t.id === profileForm.deployment_type);

    const setupStats = useMemo(() => ({
        modules: profileForm.enabled_modules.length,
        zones: profileForm.zone_templates.length,
        products: productRows.length,
        cameras: cameraRows.length,
    }), [profileForm, productRows.length, cameraRows.length]);

    const applyTemplate = (template) => {
        setProfileForm((prev) => ({
            ...prev,
            deployment_type: template.id,
            enabled_modules: template.modules || [],
            zone_templates: template.default_zones || [],
        }));
    };

    const toggleModule = (name) => {
        setProfileForm((prev) => {
            const current = new Set(prev.enabled_modules);
            if (current.has(name)) current.delete(name);
            else current.add(name);
            return { ...prev, enabled_modules: Array.from(current) };
        });
    };

    const updateZones = (value) => {
        setProfileForm((prev) => ({
            ...prev,
            zone_templates: value.split(',').map((item) => item.trim()).filter(Boolean),
        }));
    };

    const saveProfile = async (complete = false) => {
        setBusy(true); setError(null); setNotice(null);
        try {
            await setupAPI.updateProfile({
                ...profileForm,
                setup_completed: complete || Boolean(profile?.setup_completed_at),
            });
            setNotice(complete ? 'Setup marked complete.' : 'Store profile saved.');
            await refreshProfile();
        } catch (e) {
            setError(e?.response?.data?.detail || e.message);
        } finally {
            setBusy(false);
        }
    };

    const createProduct = async (e) => {
        e.preventDefault();
        if (!productForm.sku || !productForm.name) return;
        setBusy(true); setError(null); setNotice(null);
        try {
            await humanlessAPI.createProduct({ ...productForm, price: Number(productForm.price || 0) });
            setProductForm({ sku: '', name: '', category: '', price: '' });
            setNotice('Product added to catalog.');
            await refreshProducts();
        } catch (e) {
            setError(e?.response?.data?.detail || e.message);
        } finally {
            setBusy(false);
        }
    };

    const createZone = async (e) => {
        e.preventDefault();
        if (!zoneForm.zone_id || !zoneForm.name) return;
        setBusy(true); setError(null); setNotice(null);
        try {
            await humanlessAPI.createShelfZone({
                zone_id: zoneForm.zone_id,
                name: zoneForm.name,
                camera_id: zoneForm.camera_id ? Number(zoneForm.camera_id) : null,
                product_id: zoneForm.product_id ? Number(zoneForm.product_id) : null,
                current_stock: Number(zoneForm.current_stock || 0),
                low_stock_threshold: Number(zoneForm.low_stock_threshold || 3),
            });
            setZoneForm({ zone_id: '', name: '', camera_id: '', product_id: '', current_stock: 0, low_stock_threshold: 3 });
            setNotice('Shelf zone mapped.');
            await refreshZones();
        } catch (e) {
            setError(e?.response?.data?.detail || e.message);
        } finally {
            setBusy(false);
        }
    };

    return (
        <div className="page-scroll">
            <div className="page-header">
                <div>
                    <h1 className="page-title">Store Setup</h1>
                    <p className="page-subtitle">Deployment templates, catalog, zones, cameras, models, integrations, and access.</p>
                </div>
                <div style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
                    <span className={`pill ${profile?.setup_completed_at ? 'pill-success' : 'pill-warning'}`}>
                        {profile?.setup_completed_at ? 'complete' : 'first run'}
                    </span>
                    <button type="button" className="btn btn-primary btn-xs" onClick={() => saveProfile(true)} disabled={!canOperate || busy}>
                        <Check size={13} /> Complete setup
                    </button>
                </div>
            </div>

            {wizard ? <div className="alert-banner info">Finish the store profile once, then OmniTrack will open into the live overview.</div> : null}
            {error ? <div className="alert-banner danger">{error}</div> : null}
            {notice ? <div className="alert-banner info">{notice}</div> : null}

            <div className="ui-status-grid" style={{ marginBottom: 14 }}>
                <StatusTile icon={SlidersHorizontal} label="Modules" value={setupStats.modules} tone="teal" compact />
                <StatusTile icon={ClipboardList} label="Default zones" value={setupStats.zones} tone="sky" compact />
                <StatusTile icon={Boxes} label="Products" value={setupStats.products} tone="ok" compact />
                <StatusTile icon={Camera} label="Cameras" value={setupStats.cameras} tone="neutral" compact />
            </div>

            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginBottom: 14 }}>
                {TABS.map((tab) => {
                    const Icon = tab.icon;
                    return (
                        <button
                            key={tab.id}
                            type="button"
                            className={`topbar-pill ${activeTab === tab.id ? 'topbar-pill-primary' : 'topbar-pill-button'}`}
                            onClick={() => navigate(tab.path)}
                        >
                            <Icon size={13} /> {tab.label}
                        </button>
                    );
                })}
            </div>

            {activeTab === 'profile' && (
                <div className="two-col">
                    <ContentCard title="Deployment template" subtitle="Choose the closest rollout pattern, then tune modules and zones.">
                        <div className="ui-feed-list">
                            {templateRows.map((template) => (
                                <button
                                    key={template.id}
                                    type="button"
                                    className="ui-lane-row"
                                    style={{ gridTemplateColumns: '1fr 90px', textAlign: 'left' }}
                                    onClick={() => applyTemplate(template)}
                                >
                                    <span>
                                        <span className="ui-lane-row-title">{template.label}</span>
                                        <span style={{ display: 'block', color: 'var(--text-muted)', fontSize: 12 }}>
                                            {(template.modules || []).join(', ') || 'manual modules'}
                                        </span>
                                    </span>
                                    <span className={`pill ${profileForm.deployment_type === template.id ? 'pill-success' : 'pill-info'}`}>
                                        {profileForm.deployment_type === template.id ? 'active' : 'apply'}
                                    </span>
                                </button>
                            ))}
                        </div>
                    </ContentCard>

                    <ContentCard
                        title="Store profile"
                        subtitle={activeTemplate?.description || 'Manual enterprise configuration'}
                        actions={<button type="button" className="btn btn-primary btn-xs" onClick={() => saveProfile(false)} disabled={!canOperate || busy}><Save size={13} /> Save</button>}
                    >
                        <div className="settings-form">
                            <input className="form-input" value={profileForm.store_name} onChange={(e) => setProfileForm((f) => ({ ...f, store_name: e.target.value }))} />
                            <input className="form-input" value={profileForm.zone_templates.join(', ')} onChange={(e) => updateZones(e.target.value)} placeholder="entrance, aisle, checkout" />
                            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                                {MODULES.map((name) => (
                                    <button
                                        key={name}
                                        type="button"
                                        className={`pill ${profileForm.enabled_modules.includes(name) ? 'pill-success' : 'pill-info'}`}
                                        onClick={() => toggleModule(name)}
                                    >
                                        {name}
                                    </button>
                                ))}
                            </div>
                        </div>
                    </ContentCard>
                </div>
            )}

            {activeTab === 'products' && (
                <div className="two-col">
                    <ContentCard title="Products & catalog" subtitle="Catalog items used by shelf zones and cart events.">
                        <form className="settings-form" onSubmit={createProduct}>
                            <input className="form-input" placeholder="SKU" value={productForm.sku} onChange={(e) => setProductForm((f) => ({ ...f, sku: e.target.value }))} />
                            <input className="form-input" placeholder="Product name" value={productForm.name} onChange={(e) => setProductForm((f) => ({ ...f, name: e.target.value }))} />
                            <input className="form-input" placeholder="Category" value={productForm.category} onChange={(e) => setProductForm((f) => ({ ...f, category: e.target.value }))} />
                            <input className="form-input" placeholder="Price" type="number" step="0.01" value={productForm.price} onChange={(e) => setProductForm((f) => ({ ...f, price: e.target.value }))} />
                            <button type="submit" className="btn btn-primary" disabled={!canOperate || busy}><PackagePlus size={14} /> Add product</button>
                        </form>
                    </ContentCard>
                    <ContentCard title="Catalog" subtitle={`${productRows.length} product${productRows.length === 1 ? '' : 's'}`}>
                        <div className="ui-feed-list" style={{ maxHeight: 460, overflow: 'auto' }}>
                            {productRows.length === 0 ? <div className="page-empty-hint">No products yet.</div> : productRows.map((product) => (
                                <div key={product.id} className="ui-lane-row" style={{ gridTemplateColumns: '1fr 110px 88px' }}>
                                    <span className="ui-lane-row-title">{product.name}</span>
                                    <span>{product.sku}</span>
                                    <span>{money(product.price)}</span>
                                </div>
                            ))}
                        </div>
                    </ContentCard>
                </div>
            )}

            {activeTab === 'zones' && (
                <div className="two-col">
                    <ContentCard title="Shelf zones & planogram" subtitle="Map visible shelf areas to cameras and products.">
                        <form className="settings-form" onSubmit={createZone}>
                            <input className="form-input" placeholder="Zone ID, e.g. aisle1-drinks" value={zoneForm.zone_id} onChange={(e) => setZoneForm((f) => ({ ...f, zone_id: e.target.value }))} />
                            <input className="form-input" placeholder="Zone name" value={zoneForm.name} onChange={(e) => setZoneForm((f) => ({ ...f, name: e.target.value }))} />
                            <select className="form-input" value={zoneForm.camera_id} onChange={(e) => setZoneForm((f) => ({ ...f, camera_id: e.target.value }))}>
                                <option value="">No camera yet</option>
                                {cameraRows.map((camera) => <option key={camera.id} value={camera.id}>{camera.name || `Camera ${camera.id}`}</option>)}
                            </select>
                            <select className="form-input" value={zoneForm.product_id} onChange={(e) => setZoneForm((f) => ({ ...f, product_id: e.target.value }))}>
                                <option value="">No product yet</option>
                                {productRows.map((product) => <option key={product.id} value={product.id}>{product.name}</option>)}
                            </select>
                            <div className="two-col" style={{ gap: 10 }}>
                                <input className="form-input" placeholder="Current stock" type="number" value={zoneForm.current_stock} onChange={(e) => setZoneForm((f) => ({ ...f, current_stock: e.target.value }))} />
                                <input className="form-input" placeholder="Low stock threshold" type="number" value={zoneForm.low_stock_threshold} onChange={(e) => setZoneForm((f) => ({ ...f, low_stock_threshold: e.target.value }))} />
                            </div>
                            <button type="submit" className="btn btn-primary" disabled={!canOperate || busy}>Add zone</button>
                        </form>
                    </ContentCard>
                    <ContentCard title="Mapped zones" subtitle={`${zoneRows.length} shelf zone${zoneRows.length === 1 ? '' : 's'}`}>
                        <div className="ui-feed-list" style={{ maxHeight: 460, overflow: 'auto' }}>
                            {zoneRows.length === 0 ? <div className="page-empty-hint">No shelf zones yet.</div> : zoneRows.map((zone) => (
                                <div key={zone.id} className="ui-lane-row" style={{ gridTemplateColumns: '1fr 90px 90px' }}>
                                    <span className="ui-lane-row-title">{zone.name}</span>
                                    <span>Cam {zone.camera_id || '-'}</span>
                                    <span>{zone.current_stock} left</span>
                                </div>
                            ))}
                        </div>
                    </ContentCard>
                </div>
            )}

            {activeTab === 'cameras' && (
                <ContentCard title="Cameras & zones" subtitle="Camera CRUD remains available from the API; use Video Feeds to start live processing.">
                    <div className="ui-feed-list">
                        {cameraRows.length === 0 ? <div className="page-empty-hint">No registered cameras yet. Add a video feed from Operations.</div> : cameraRows.map((camera) => (
                            <div key={camera.id} className="ui-lane-row" style={{ gridTemplateColumns: '1fr 120px 120px 120px' }}>
                                <span className="ui-lane-row-title">{camera.name || `Camera ${camera.id}`}</span>
                                <span>{camera.zone || 'default'}</span>
                                <span>{camera.camera_type || 'general'}</span>
                                <span className={`pill ${camera.is_active ? 'pill-success' : 'pill-warning'}`}>{camera.is_active ? 'active' : 'inactive'}</span>
                            </div>
                        ))}
                    </div>
                    <button type="button" className="btn btn-secondary btn-xs" style={{ marginTop: 12 }} onClick={() => navigate('/detection')}>
                        Open Video Feeds
                    </button>
                </ContentCard>
            )}

            {activeTab === 'models' && (
                <ContentCard title="Models & detection policy" subtitle="Zone-smart ensemble defaults are selected by the backend when Smart auto ensemble is used.">
                    <div className="ui-feed-list">
                        {modelRows.length === 0 ? <div className="page-empty-hint">No model weights found in the configured model directory.</div> : modelRows.map((model) => (
                            <div key={model.filename} className="ui-lane-row" style={{ gridTemplateColumns: '1fr 100px 100px' }}>
                                <span className="ui-lane-row-title">{model.filename}</span>
                                <span>{model.size_mb ? `${model.size_mb} MB` : 'ready'}</span>
                                <span className="pill pill-info">{model.loaded ? 'loaded' : 'available'}</span>
                            </div>
                        ))}
                    </div>
                </ContentCard>
            )}

            {activeTab === 'integrations' && (
                <ContentCard title="Integrations & alerts" subtitle="Plugins, Redis, websocket, and deployment health.">
                    <div className="ui-status-grid">
                        <StatusTile icon={ShieldCheck} label="API health" value={health?.status || 'checking'} tone={health?.status === 'healthy' ? 'ok' : 'warn'} />
                        <StatusTile icon={LinkIcon} label="Redis" value={health?.components?.redis?.status || health?.components?.redis || 'unknown'} tone="sky" />
                        <StatusTile icon={SlidersHorizontal} label="Plugins" value={(health?.components?.plugins?.loaded || []).length} tone="teal" />
                    </div>
                    <div className="ui-feed-list" style={{ marginTop: 14 }}>
                        {(health?.components?.plugins?.loaded || []).length === 0 ? <div className="page-empty-hint">No plugins enabled.</div> : health.components.plugins.loaded.map((plugin) => (
                            <div key={plugin} className="ui-lane-row" style={{ gridTemplateColumns: '1fr 120px' }}>
                                <span className="ui-lane-row-title">{plugin}</span>
                                <span className="pill pill-success">enabled</span>
                            </div>
                        ))}
                    </div>
                </ContentCard>
            )}

            {activeTab === 'users' && (
                <ContentCard title="Users & access" subtitle="Role-aware navigation is active; deeper RBAC remains enforced by backend dependencies.">
                    <div className="ui-status-grid">
                        <StatusTile icon={Users} label="Your role" value={role} tone="teal" />
                        <StatusTile icon={ShieldCheck} label="Admin tools" value={canAdmin ? 'full' : 'limited'} tone={canAdmin ? 'ok' : 'warn'} />
                        <StatusTile icon={Store} label="Setup access" value={canOperate ? 'editable' : 'read-only'} tone={canOperate ? 'ok' : 'neutral'} />
                    </div>
                    <button
                        type="button"
                        className="btn btn-secondary btn-xs"
                        style={{ marginTop: 12 }}
                        onClick={() => {
                            const adminUrl = `${window.location.protocol}//${window.location.hostname}:8000/admin`;
                            window.open(adminUrl, '_blank', 'noopener,noreferrer');
                        }}
                        disabled={!canAdmin}
                    >
                        Open SQLAdmin
                    </button>
                </ContentCard>
            )}
        </div>
    );
}
