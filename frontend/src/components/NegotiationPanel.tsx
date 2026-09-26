/**
 * The negotiation panel: the real bids the backend collected.
 *
 * Each row is an actual `BidSubmitted` event, so the cost breakdown, the
 * validity window, and the winning assignment are the backend's own numbers.
 * The panel only groups them by task and sorts by cost, and it never re-scores
 * anything itself.
 */

import type { NegotiationRecord } from "../state/useEventLog";
import { formatNumber, shortRobotId } from "../state/format";
import { PALETTE } from "../styles/palette";

export interface NegotiationPanelProps {
  negotiations: NegotiationRecord[];
  onSelectRobot: (robotId: string) => void;
}

export function NegotiationPanel({ negotiations, onSelectRobot }: NegotiationPanelProps) {
  if (negotiations.length === 0) {
    return (
      <div className="panel-body panel-body--empty">
        <p>no negotiation rounds yet</p>
        <p className="muted">
          press <strong>DISPATCH TASKS</strong> to run a round, or submit a task from the task
          panel. Each <code>BID_SUBMITTED</code> event appears here with the backend&apos;s own
          cost breakdown.
        </p>
      </div>
    );
  }

  return (
    <div className="panel-body negotiation">
      {negotiations.map((record) => {
        // Sorted by the backend's total cost: cheapest bid first.
        const bids = [...record.bids].sort((left, right) => left.totalCost - right.totalCost);
        const winner = record.assignedRobotId;
        return (
          <section key={record.taskId} className="negotiation-card">
            <header>
              <h3 className="negotiation-task">{record.taskId}</h3>
              <span className="negotiation-status" data-status={record.status}>
                {record.status}
              </span>
            </header>
            {bids.length === 0 ? (
              <p className="muted">no bids captured</p>
            ) : (
              <table className="bid-table">
                <thead>
                  <tr>
                    <th>UNIT</th>
                    <th>TOTAL</th>
                    <th>DIST</th>
                    <th>BATT</th>
                    <th>LOAD</th>
                    <th>ETA</th>
                    <th>VALID TO</th>
                  </tr>
                </thead>
                <tbody>
                  {bids.map((bid) => {
                    const won = winner === bid.robotId;
                    return (
                      <tr
                        key={`${record.taskId}-${bid.robotId}-${bid.sequence}`}
                        className={won ? "bid-row bid-row--won" : "bid-row"}
                        onClick={() => onSelectRobot(bid.robotId)}
                      >
                        <th scope="row">
                          {shortRobotId(bid.robotId)}
                          {won ? <span className="bid-crown"> WIN</span> : null}
                        </th>
                        <td style={{ color: won ? PALETTE.selected : undefined }}>
                          {formatNumber(bid.totalCost, 2)}
                        </td>
                        <td>{formatNumber(bid.distanceCost, 2)}</td>
                        <td>{formatNumber(bid.batteryCost, 2)}</td>
                        <td>{formatNumber(bid.workloadCost, 2)}</td>
                        <td>{formatNumber(bid.completionS, 1)}s</td>
                        <td>{formatNumber(bid.validUntilS, 1)}s</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            )}
            {winner ? (
              <p className="negotiation-assigned">
                assigned to <strong>{winner}</strong>
              </p>
            ) : (
              <p className="muted">no assignment yet</p>
            )}
          </section>
        );
      })}
    </div>
  );
}
