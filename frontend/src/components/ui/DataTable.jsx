import React from 'react';

export default function DataTable({ columns, rows, empty, sticky = true, className = '' }) {
    if (!rows?.length) {
        return empty || null;
    }

    return (
        <div className={`ui-table-wrap ${sticky ? 'ui-table-wrap--sticky' : ''} ${className}`.trim()}>
            <table className="ui-table">
                <thead>
                    <tr>
                        {columns.map((col) => (
                            <th key={col.key} style={col.width ? { width: col.width } : undefined}>
                                {col.label}
                            </th>
                        ))}
                    </tr>
                </thead>
                <tbody>
                    {rows.map((row, ri) => (
                        <tr key={row.id ?? ri}>
                            {columns.map((col) => (
                                <td key={col.key}>
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
