/**
 * OmniTrack AI — Peak Hours (live)
 * Polls /api/peak-hours/today.
 */

import React, { useMemo, useState } from 'react';
import { motion } from 'framer-motion';
import { TrendingUp, Clock, Users } from 'lucide-react';
import {
    AreaChart, Area, ResponsiveContainer, XAxis, YAxis, Tooltip, CartesianGrid, ReferenceLine,
} from 'recharts';
import { peakHoursAPI } from '../services/api';
import useLivePoll from '../hooks/useLivePoll';

export default function PeakHoursPage() {
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
                <Stat icon={Users} label="Total Visitors" value={Number(totalVisitors).toLocaleString()} accent="indigo" />
                <Stat
                    icon={Clock}
                    label="Peak Hour"
                    value={peakHour != null ? `${String(peakHour).padStart(2, '0')}:00` : '—'}
                    accent="gold"
                />
                <Stat
                    icon={TrendingUp}
                    label="Peak Visitors"
                    value={Number(peakCount || 0).toLocaleString()}
                    accent="rose"
                />
                <Stat
                    icon={Users}
                    label="Busiest Zone"
                    value={hourly.find((h) => h.hour === peakHour)?.busiest_zone || '—'}
                    accent="cyan"
                />
            </div>

            <div className="card">
                <div className="card-header">
                    <h3 className="card-title">Hourly Traffic</h3>
                </div>
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
                                        <stop offset="0%" stopColor="#22d3ee" stopOpacity={0.5} />
                                        <stop offset="100%" stopColor="#22d3ee" stopOpacity={0} />
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
                                <Area type="monotone" dataKey="visitors" stroke="#22d3ee" strokeWidth={2} fill="url(#peakA)" />
                            </AreaChart>
                        </ResponsiveContainer>
                    </div>
                )}
            </div>
        </div>
    );
}

function Stat({ icon: Icon, label, value, accent = 'indigo' }) {
    return (
        <motion.div className="stat-card" whileHover={{ y: -2 }}>
            <div className={`stat-icon stat-icon-${accent}`}><Icon size={18} /></div>
            <div className="stat-label">{label}</div>
            <div className="stat-value">{value}</div>
        </motion.div>
    );
}
