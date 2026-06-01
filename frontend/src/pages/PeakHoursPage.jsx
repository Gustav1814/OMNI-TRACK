/**
 * OmniTrack AI — Peak Hours (live)
 * Polls /api/peak-hours/today.
 */

import React, { useMemo, useState } from 'react';
import { TrendingUp, Clock, Users } from 'lucide-react';
import {
    AreaChart, Area, ResponsiveContainer, XAxis, YAxis, Tooltip, CartesianGrid, ReferenceLine,
} from 'recharts';
import { peakHoursAPI } from '../services/api';
import useLivePoll from '../hooks/useLivePoll';
import StatCard from '../components/ui/StatCard';
import ContentCard from '../components/ui/ContentCard';
import useGradientColors from '../hooks/useGradientColors';

export default function PeakHoursPage() {
    const { b } = useGradientColors();
    const [zone, setZone] = useState('');
    const { data } = useLivePoll(
        () => peakHoursAPI.today(zone || undefined),
        { intervalMs: 60000 }
    );

    const hourly = Array.isArray(data?.hourly_data) ? data.hourly_data : [];

    const chart = useMemo(() => hourly.map((h) => ({
        hour: `${String(h.hour).padStart(2, '0')}:00`,
        visitors: h.visitor_count,
        dwell: h.avg_dwell_time,
    })), [hourly]);

    const peakHour = data?.peak_hour;
    const peakCount = data?.peak_count;
    const totalVisitors = data?.total_visitors ?? hourly.reduce((a, h) => a + (h.visitor_count || 0), 0);

    return (
        <div className="page-scroll">
            <div className="page-header">
                <div>
                    <h1 className="page-title">Peak Hours</h1>
                    <p className="page-subtitle">Foot-traffic patterns today · {data?.date || 'today'}</p>
                </div>
                <input
                    className="form-input"
                    placeholder="Filter by zone…"
                    style={{ maxWidth: 240 }}
                    value={zone}
                    onChange={(e) => setZone(e.target.value)}
                />
            </div>

            <div className="stats-grid">
                <StatCard icon={Users} label="Total Visitors" value={Number(totalVisitors).toLocaleString()} accent="teal" />
                <StatCard
                    icon={Clock}
                    label="Peak Hour"
                    value={peakHour != null ? `${String(peakHour).padStart(2, '0')}:00` : '—'}
                    accent="coral"
                />
                <StatCard
                    icon={TrendingUp}
                    label="Peak Visitors"
                    value={Number(peakCount || 0).toLocaleString()}
                    accent="rose"
                />
                <StatCard
                    icon={Users}
                    label="Busiest Zone"
                    value={hourly.find((h) => h.hour === peakHour)?.busiest_zone || '—'}
                    accent="cyan"
                />
            </div>

            <ContentCard title="Hourly Traffic" accent="sky">
                {chart.length === 0 ? (
                    <div className="page-empty-hint">
                        No traffic samples yet for today. Start the pipeline to record foot-traffic rows.
                    </div>
                ) : (
                    <div style={{ height: 320 }}>
                        <ResponsiveContainer>
                            <AreaChart data={chart}>
                                <defs>
                                    <linearGradient id="peakA" x1="0" y1="0" x2="0" y2="1">
                                        <stop offset="0%" stopColor={b} stopOpacity={0.5} />
                                        <stop offset="100%" stopColor={b} stopOpacity={0} />
                                    </linearGradient>
                                </defs>
                                <CartesianGrid stroke="rgba(255,255,255,0.05)" />
                                <XAxis dataKey="hour" stroke="#71717a" fontSize={11} />
                                <YAxis stroke="#71717a" fontSize={11} />
                                <Tooltip contentStyle={{ background: '#111', border: '1px solid #222' }} />
                                {peakHour != null && (
                                    <ReferenceLine
                                        x={`${String(peakHour).padStart(2, '0')}:00`}
                                        stroke="#f43f5e"
                                        strokeDasharray="3 3"
                                        label={{ value: 'Peak', fill: '#f43f5e', fontSize: 10 }}
                                    />
                                )}
                                <Area type="monotone" dataKey="visitors" stroke={b} strokeWidth={2} fill="url(#peakA)" />
                            </AreaChart>
                        </ResponsiveContainer>
                    </div>
                )}
            </ContentCard>
        </div>
    );
}
