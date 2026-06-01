/**

 * KPI card — animated value, delta badge, accent edge.

 */

import React from 'react';

import CountUp from './CountUp';

import DeltaBadge from './ui/DeltaBadge';

import { resolveKpiAccentRgb } from '../lib/kpiAccentColors';



function isNumeric(value) {

    if (typeof value === 'number') return true;

    if (typeof value === 'string' && /^[\d,]+(\.\d+)?$/.test(value.replace(/,/g, ''))) return true;

    return false;

}



function parseNumeric(value) {

    if (typeof value === 'number') return value;

    return parseFloat(String(value).replace(/,/g, '')) || 0;

}



export default function KPICard({

    icon: Icon,

    label,

    value,

    suffix,

    accent = 'teal',

    progress = 0,

    tag = 'Today',

    hint,

    delta,

    deltaLabel,

    loading = false,

}) {

    const rgb = resolveKpiAccentRgb(accent);

    const numeric = isNumeric(value);



    if (loading) {

        return (

            <div className="exec-kpi exec-kpi-skeleton">

                <div className="ui-skeleton-kpi">

                    <div className="ui-skeleton ui-skeleton-icon" />

                    <div className="ui-skeleton ui-skeleton-line ui-skeleton-line--sm" />

                    <div className="ui-skeleton ui-skeleton-line ui-skeleton-line--lg" />

                    <div className="ui-skeleton ui-skeleton-bar" />

                </div>

            </div>

        );

    }



    return (

        <div className={`exec-kpi exec-kpi-${accent}`}>

            <div className="exec-kpi-inner" style={{ '--kpi-rgb': rgb }}>

                <div className="exec-kpi-top">

                    <div className={`exec-kpi-icon exec-kpi-icon-${accent}`}>

                        <Icon size={16} strokeWidth={2} />

                    </div>

                    <span className="exec-kpi-tag">{tag}</span>

                </div>

                <div className="exec-kpi-body">

                    <span className="exec-kpi-label">{label}</span>

                    <div className="exec-kpi-value-row">

                        <div className="exec-kpi-value">

                            {numeric ? (

                                <>

                                    <CountUp

                                        value={parseNumeric(value)}

                                        format={(n) => (Number.isInteger(n) ? n.toLocaleString() : n.toFixed(1))}

                                    />

                                    {suffix ? <span className="exec-kpi-suffix">{suffix}</span> : null}

                                </>

                            ) : (

                                <>

                                    {value}

                                    {suffix ? <span className="exec-kpi-suffix">{suffix}</span> : null}

                                </>

                            )}

                        </div>

                        {delta != null ? <DeltaBadge value={delta} label={deltaLabel} size="xs" /> : null}

                    </div>

                    {hint ? <span className="exec-kpi-hint">{hint}</span> : null}

                </div>

                <div className="exec-kpi-foot">

                    <div className="exec-kpi-bar">

                        <span style={{ width: `${Math.max(4, Math.min(100, progress))}%` }} />

                    </div>

                </div>

            </div>

        </div>

    );

}


