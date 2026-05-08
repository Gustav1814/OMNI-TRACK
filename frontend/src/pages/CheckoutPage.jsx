/**
 * OmniTrack AI — Checkout (live)
 * Polls /api/checkout/metrics and /api/checkout/summary.
 */

import React from 'react';
import { motion } from 'framer-motion';
import { ShoppingCart, Clock, TrendingUp } from 'lucide-react';
import {
    BarChart, Bar, ResponsiveContainer, XAxis, YAxis, Tooltip, CartesianGrid, Cell,
} from 'recharts';
import { checkoutAPI } from '../services/api';
import useLivePoll from '../hooks/useLivePoll';

export default function CheckoutPage() {
    const { data: metrics } = useLivePoll(() => checkoutAPI.metrics(), { intervalMs: 4000 });
    const { data: summary } = useLivePoll(() => checkoutAPI.summary(), { intervalMs: 10000 });

    const lanes = Array.isArray(metrics) ? metrics : [];
    const chartData = lanes.map((l) => ({
        name: l.lane_id,
        queue: l.queue_length,
        wait: l.current_wait_estimate,
    }));

    const totalQueue = lanes.reduce((a, l) => a + (l.queue_length || 0), 0);
    const avgWait = lanes.length
        ? lanes.reduce((a, l) => a + (l.current_wait_estimate || 0), 0) / lanes.length
        : 0;
    const avgService = lanes.length
        ? lanes.reduce((a, l) => a + (l.avg_service_time || 0), 0) / lanes.length
        : 0;
    const throughput = lanes.reduce((a, l) => a + (l.throughput || 0), 0);

    return (
        <div className="page-scroll">
            <div className="page-header">
                <div>
                    <h1 className="page-title">Checkout Analytics</h1>
                    <p className="page-subtitle">Queue lengths, wait times, service times, throughput</p>
                </div>
            </div>

            <div className="stats-grid">
                <Stat icon={ShoppingCart} label="Total in Queue" value={totalQueue} accent="indigo" />
                <Stat icon={Clock} label="Avg Wait" value={avgWait.toFixed(1)} suffix="s" accent="amber" />
                <Stat icon={Clock} label="Avg Service Time" value={avgService.toFixed(1)} suffix="s" accent="cyan" />
                <Stat icon={TrendingUp} label="Throughput" value={Math.round(throughput)} suffix=" /hr" accent="emerald" />
            </div>

            <div className="card">
                <div className="card-header">
                    <h3 className="card-title">Per-Lane Queue Length</h3>
                    <div className="card-subtitle">Updates every 4s</div>
                </div>
                {chartData.length === 0 ? (
                    <div className="page-empty-hint">
                        No checkout lanes configured. Add cameras with checkout zones to see live metrics.
                    </div>
                ) : (
                    <div style={{ height: 300 }}>
                        <ResponsiveContainer>
                            <BarChart data={chartData}>
                                <CartesianGrid stroke="rgba(255,255,255,0.05)" />
                                <XAxis dataKey="name" stroke="#71717a" fontSize={11} />
                                <YAxis stroke="#71717a" fontSize={11} />
                                <Tooltip contentStyle={{ background: '#111', border: '1px solid #222' }} />
                                <Bar dataKey="queue" radius={[6, 6, 0, 0]} fill="#6366f1">
                                    {chartData.map((_, i) => <Cell key={i} fill="#6366f1" />)}
                                </Bar>
                            </BarChart>
                        </ResponsiveContainer>
                    </div>
                )}
            </div>

            <div className="card" style={{ marginTop: 18 }}>
                <div className="card-header">
                    <h3 className="card-title">Lanes</h3>
                </div>
                <div style={{ display: 'grid', gap: 6 }}>
                    {lanes.map((l) => (
                        <div key={l.lane_id} style={{
                            display: 'grid',
                            gridTemplateColumns: '1fr repeat(4, 120px)',
                            gap: 10, alignItems: 'center',
                            padding: '10px 12px',
                            background: 'var(--bg-glass)',
                            border: '1px solid var(--border)', borderRadius: 10,
                        }}>
                            <div>
                                <div style={{ fontWeight: 600 }}>Lane {l.lane_id}</div>
                                <div style={{ fontSize: 11, color: 'var(--text-muted)' }}>cam {l.camera_id}</div>
                            </div>
                            <Cell2 label="Queue" value={l.queue_length} />
                            <Cell2 label="Wait" value={`${(l.current_wait_estimate || 0).toFixed(0)}s`} />
                            <Cell2 label="Service" value={`${(l.avg_service_time || 0).toFixed(0)}s`} />
                            <Cell2 label="Throughput" value={`${Math.round(l.throughput || 0)}/hr`} />
                        </div>
                    ))}
                </div>
            </div>

            {summary && (
                <div className="card" style={{ marginTop: 18 }}>
                    <div className="card-header">
                        <h3 className="card-title">Summary</h3>
                    </div>
                    <pre style={{ fontSize: 12, color: 'var(--text-secondary)' }}>
                        {JSON.stringify(summary, null, 2)}
                    </pre>
                </div>
            )}
        </div>
    );
}

function Stat({ icon: Icon, label, value, suffix = '', accent = 'indigo' }) {
    return (
        <motion.div className="stat-card" whileHover={{ y: -2 }}>
            <div className={`stat-icon stat-icon-${accent}`}><Icon size={18} /></div>
            <div className="stat-label">{label}</div>
            <div className="stat-value">{value}{suffix}</div>
        </motion.div>
    );
}

function Cell2({ label, value }) {
    return (
        <div style={{ textAlign: 'center' }}>
            <div style={{ fontSize: 11, color: 'var(--text-muted)' }}>{label}</div>
            <div style={{ fontSize: 14, fontWeight: 600 }}>{value}</div>
        </div>
    );
}
