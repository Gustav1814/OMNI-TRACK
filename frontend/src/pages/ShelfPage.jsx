/**
 * OmniTrack AI — Shelf Engagement (live)
 * Polls /api/shelf/engagement + /api/shelf/top-zones.
 */

import React from 'react';
import { ShoppingBag, Clock, Award } from 'lucide-react';
import {
    BarChart, Bar, ResponsiveContainer, XAxis, YAxis, Tooltip, CartesianGrid,
} from 'recharts';
import { shelfAPI } from '../services/api';
import useLivePoll from '../hooks/useLivePoll';
import StatCard from '../components/ui/StatCard';
import ContentCard from '../components/ui/ContentCard';

const CORAL = '#f97316';

export default function ShelfPage() {
    const { data: engagement } = useLivePoll(() => shelfAPI.engagement(), { intervalMs: 5000 });
    const { data: topZones } = useLivePoll(() => shelfAPI.topZones(), { intervalMs: 10000 });

    const zones = Array.isArray(engagement) ? engagement : [];
    const chartData = zones.slice(0, 10).map((z) => ({
        name: z.zone_name,
        score: z.engagement_score,
        dwell: z.avg_dwell_time,
    }));

    const topZonesList = Array.isArray(topZones) ? topZones : (topZones?.top_zones || []);

    return (
        <div className="page-scroll">
            <div className="page-header">
                <div>
                    <h1 className="page-title">Shelf Engagement</h1>
                    <p className="page-subtitle">Dwell time · visits · top-selling zones</p>
                </div>
            </div>

            <div className="stats-grid">
                <StatCard icon={ShoppingBag} label="Zones Tracked" value={zones.length} accent="teal" />
                <StatCard
                    icon={Clock}
                    label="Avg Dwell"
                    value={zones.length ? (zones.reduce((a, z) => a + (z.avg_dwell_time || 0), 0) / zones.length).toFixed(1) : 0}
                    suffix="s"
                    accent="cyan"
                />
                <StatCard
                    icon={Award}
                    label="Top Zone"
                    value={zones[0]?.zone_name || '—'}
                    accent="coral"
                />
                <StatCard
                    icon={ShoppingBag}
                    label="Total Visits"
                    value={zones.reduce((a, z) => a + (z.visit_count || 0), 0)}
                    accent="emerald"
                />
            </div>

            <ContentCard title="Engagement Score by Zone" subtitle="Top 10 shelves" accent="coral">
                {chartData.length === 0 ? (
                    <div className="page-empty-hint">
                        No engagement data yet — run the pipeline on a clip with a shelf camera.
                    </div>
                ) : (
                    <div style={{ height: 340 }}>
                        <ResponsiveContainer>
                            <BarChart data={chartData} layout="vertical">
                                <CartesianGrid stroke="rgba(255,255,255,0.05)" />
                                <XAxis type="number" stroke="#71717a" fontSize={11} />
                                <YAxis dataKey="name" type="category" stroke="#71717a" fontSize={11} width={120} />
                                <Tooltip contentStyle={{ background: '#111', border: '1px solid #222' }} />
                                <Bar dataKey="score" fill={CORAL} radius={[0, 6, 6, 0]} />
                            </BarChart>
                        </ResponsiveContainer>
                    </div>
                )}
            </ContentCard>

            <div className="two-col">
                <ContentCard title="All Zones" accent="teal">
                    <div className="ui-feed-list" style={{ maxHeight: 360, overflow: 'auto' }}>
                        {zones.map((z) => (
                            <div
                                key={z.zone_id}
                                className="ui-lane-row"
                                style={{ gridTemplateColumns: '40px 1fr 80px 80px 80px' }}
                            >
                                <span className="pill pill-info" style={{ justifyContent: 'center' }}>#{z.rank}</span>
                                <span className="ui-lane-row-title">{z.zone_name}</span>
                                <span style={{ fontSize: 12 }}>{z.visit_count} visits</span>
                                <span style={{ fontSize: 12 }}>{z.avg_dwell_time?.toFixed(1)}s</span>
                                <span style={{ fontSize: 12, fontWeight: 600, color: CORAL }}>
                                    {z.engagement_score?.toFixed(1)}
                                </span>
                            </div>
                        ))}
                    </div>
                </ContentCard>

                <ContentCard title="Top Zones" subtitle="From /shelf/top-zones" accent="sky">
                    <pre className="ui-code-block">
                        {topZonesList.length ? JSON.stringify(topZonesList, null, 2) : '—'}
                    </pre>
                </ContentCard>
            </div>
        </div>
    );
}
