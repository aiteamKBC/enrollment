import { CalendarDays, Home, Mail, PenLine, Phone, Users } from 'lucide-react';

export function EnrolmentProfileArtwork() {
  return <>
    <svg className="ep-hero-wave" viewBox="0 0 1500 145" preserveAspectRatio="none" aria-hidden="true">
      <defs><linearGradient id="ep-gold-wave"><stop stopColor="#f6dc90" /><stop offset="1" stopColor="#f3c89c" /></linearGradient></defs>
      <path fill="#d4afd9" fillOpacity=".25" d="M0 145Q300 85 600 118T1000 105T1500 30V145Z" />
      <path fill="url(#ep-gold-wave)" d="M0 145C220 144 340 70 560 125S880 145 1020 112S1310 115 1500 88V145Z" />
      <path fill="#a165b6" fillOpacity=".65" d="M0 145C220 150 340 76 560 130S880 150 1020 118S1310 125 1500 100V145Z" />
    </svg>
    <img className="ep-hero-artwork" src="/images/enrolment-details/enrolment-details-hero.png" alt="" />
  </>;
}

export function ContactFactIcon({ label }: { label: string }) {
  const icons: Record<string, typeof Mail> = { Email: Mail, Phone, 'Date of birth': CalendarDays, Group: Users, 'Home address': Home, Signature: PenLine };
  const Icon = icons[label];
  return Icon ? <Icon className="ep-fact-icon" size={21} aria-hidden="true" /> : null;
}
