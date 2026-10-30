import { AppIcon } from '@/components/feature/AppIcon';
import styles from './ActivityElapsedTimer.module.css';

export function ActivityElapsedTimer({ time }: { time: string }) {
  const [hours = '00', minutes = '00', seconds = '00'] = time.split(':');
  const parts = [
    { value: hours, label: 'Hours' },
    { value: minutes, label: 'Minutes' },
    { value: seconds, label: 'Seconds' },
  ];

  return (
    <div
      className={styles.timer}
      role="timer"
      aria-live="off"
      aria-label={`Time on this activity: ${hours} hours, ${minutes} minutes, ${seconds} seconds`}
    >
      <div className={styles.heading} aria-hidden="true">
        <span className={styles.icon}><AppIcon className="ri-timer-line" /></span>
        <span>Time on this activity</span>
        <span className={styles.liveDot} />
      </div>
      <div className={styles.parts} aria-hidden="true">
        {parts.map(({ value, label }) => (
          <div key={label} className={styles.part}>
            <span className={styles.value}>{value}</span>
            <span className={styles.label}>{label}</span>
          </div>
        ))}
      </div>
    </div>
  );
}
