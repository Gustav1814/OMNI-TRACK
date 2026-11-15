import React from 'react';

/** `.data-table` in a horizontal scroller. `col.num` right-aligns a numeric column. */
export default function DataTable({ columns, rows, empty, sticky = true, className = '' }) {
    if (!rows?.length) {
        return empty || null;
    }

    return (
        <div className={`table-scroll ui-table-wrap ${sticky ? 'ui-table-wrap--sticky' : ''} ${className}`.trim()}>
            <table className="data-table ui-table">
                <thead>
                    <tr>
                        {columns.map((col) => (
                            <th key={col.key} className={col.num ? 'num' : undefined} style={col.width ? { width: col.width } : undefined}>
                                {col.label}
                            </th>
                        ))}
                    </tr>
                </thead>
                <tbody>
                    {rows.map((row, ri) => (
                        <tr key={row.id ?? ri}>
                            {columns.map((col) => (
                                <td key={col.key} className={col.num ? 'num' : undefined}>
                                    {col.render ? col.render(row[col.key], row) : row[col.key]}
                                </td>
                            ))}
                        </tr>
                    ))}
                </tbody>
            </table>
        </div>
    );
}
