/**
 * OmniTrack AI — Checkout (live)
 * Polls /api/checkout/metrics and /api/checkout/summary.
 */

import React from 'react';
import { ShoppingCart, Clock, TrendingUp } from 'lucide-react';
import {
    BarChart, Bar, ResponsiveContainer, XAxis, YAxis, Tooltip, CartesianGrid, Cell,
} from 'recharts';
import { checkoutAPI } from '../services/api';
import useLivePoll from '../hooks/useLivePoll';
import useGradientColors from '../hooks/useGradientColors';
import StatCard from '../components/ui/StatCard';
import ContentCard from '../components/ui/ContentCard';

export default function CheckoutPage() {
    const { a } = useGradientColors();
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
                <StatCard icon={ShoppingCart} label="Total in Queue" value={totalQueue} accent="teal" />
                <StatCard icon={Clock} label="Avg Wait" value={avgWait.toFixed(1)} suffix="s" accent="coral" />
                <StatCard icon={Clock} label="Avg Service Time" value={avgService.toFixed(1)} suffix="s" accent="cyan" />
                <StatCard icon={TrendingUp} label="Throughput" value={Math.round(throughput)} suffix=" /hr" accent="emerald" />
            </div>

            <ContentCard title="Per-Lane Queue Length" subtitle="Updates every 4s" accent="sky">
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
                                <Bar dataKey="queue" radius={[6, 6, 0, 0]} fill={a}>
                                    {chartData.map((_, i) => <Cell key={i} fill={a} />)}
                                </Bar>
                            </BarChart>
                        </ResponsiveContainer>
                    </div>
                )}
            </ContentCard>

            <ContentCard title="Lanes" accent="teal">
                <div className="ui-feed-list">
                    {lanes.map((l) => (
                        <div
                            key={l.lane_id}
                            className="ui-lane-row"
                            style={{ gridTemplateColumns: '1fr repeat(4, 120px)' }}
                        >
                            <div>
                                <div className="ui-lane-row-title">Lane {l.lane_id}</div>
                                <div className="ui-lane-row-sub">cam {l.camera_id}</div>
                            </div>
                            <Cell2 label="Queue" value={l.queue_length} />
                            <Cell2 label="Wait" value={`${(l.current_wait_estimate || 0).toFixed(0)}s`} />
                            <Cell2 label="Service" value={`${(l.avg_service_time || 0).toFixed(0)}s`} />
                            <Cell2 label="Throughput" value={`${Math.round(l.throughput || 0)}/hr`} />
                        </div>
                    ))}
                </div>
            </ContentCard>

            {summary && (
                <ContentCard title="Summary" accent="cyan">
                    <pre className="ui-code-block">{JSON.stringify(summary, null, 2)}</pre>
                </ContentCard>
            )}
        </div>
    );
}


function Cell2({ label, value }) {
    return (
        <div style={{ textAlign: 'center' }}>
            <div className="ui-lane-row-label">{label}</div>
            <div className="ui-lane-row-value">{value}</div>
        </div>
    );
}
