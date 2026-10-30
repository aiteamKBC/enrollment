import { Fragment, type ReactNode } from 'react';
import { parseRich, type RichBlock, type RichInline } from './richFormat';

/**
 * Renders the wizard's formatted text (see richFormat.ts for the format) as
 * plain React elements — never as HTML — so text written in the builder cannot
 * inject markup or script into a learner's page.
 */
export interface RichTextClasses {
  container?: string;
  heading?: string;
  subheading?: string;
  paragraph?: string;
  list?: string;
  link?: string;
}

const DEFAULT_CLASSES: Required<RichTextClasses> = {
  container: 'space-y-2',
  heading: 'font-heading text-[15px] font-semibold text-primary-600 sm:text-base pt-2',
  subheading: 'font-heading text-[13px] font-semibold text-foreground-800 pt-1',
  paragraph: '',
  list: 'list-disc space-y-1 pl-6',
  link: 'text-primary-600 hover:underline break-words',
};

function Inline({ nodes, linkClass }: { nodes: RichInline[]; linkClass: string }) {
  return (
    <>
      {nodes.map((n, i) => {
        switch (n.type) {
          case 'text':
            return <Fragment key={i}>{n.text}</Fragment>;
          case 'break':
            return <br key={i} />;
          case 'bold':
            return <strong key={i} className="font-semibold text-foreground-800"><Inline nodes={n.children} linkClass={linkClass} /></strong>;
          case 'italic':
            return <em key={i}><Inline nodes={n.children} linkClass={linkClass} /></em>;
          case 'link': {
            const external = /^https?:/i.test(n.href);
            return (
              <a key={i} href={n.href} className={linkClass} {...(external ? { target: '_blank', rel: 'noopener noreferrer' } : {})}>
                <Inline nodes={n.children} linkClass={linkClass} />
              </a>
            );
          }
          default:
            return null;
        }
      })}
    </>
  );
}

function Block({ block, classes }: { block: RichBlock; classes: Required<RichTextClasses> }): ReactNode {
  switch (block.type) {
    case 'heading':
      return block.level === 2
        ? <h3 className={classes.heading}><Inline nodes={block.children} linkClass={classes.link} /></h3>
        : <h4 className={classes.subheading}><Inline nodes={block.children} linkClass={classes.link} /></h4>;
    case 'list': {
      const Tag = block.ordered ? 'ol' : 'ul';
      return (
        <Tag className={block.ordered ? classes.list.replace('list-disc', 'list-decimal') : classes.list}>
          {block.items.map((item, i) => <li key={i}><Inline nodes={item} linkClass={classes.link} /></li>)}
        </Tag>
      );
    }
    default:
      return <p className={classes.paragraph || undefined}><Inline nodes={block.children} linkClass={classes.link} /></p>;
  }
}

export function RichText({ source, classes }: { source: string; classes?: RichTextClasses }) {
  const merged = { ...DEFAULT_CLASSES, ...classes };
  return (
    <div className={merged.container}>
      {parseRich(source).map((block, i) => <Block key={i} block={block} classes={merged} />)}
    </div>
  );
}
