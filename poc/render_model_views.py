"""Render model front, top, and isometric views as dependency-free SVG files."""
import argparse
import html
import json
import math
from pathlib import Path

W, H = 1400, 900


def value(item, key):
    v = item.get('dimensions', {}).get(key)
    return v if isinstance(v, (int, float)) and v > 0 else None


def render(model_path, out):
    model = json.loads(model_path.read_text(encoding='utf-8-sig'))
    room = model.get('room', {})
    walls, products, features = room.get('walls', []), room.get('products', []), room.get('features', [])
    out.mkdir(parents=True, exist_ok=False)
    title = html.escape(model.get('project', {}).get('name', 'Model'))
    for view in ('front', 'top', '3d'):
        body = [f'<text x="28" y="38" font-size="25">{view.title()} — {title} ({html.escape(model.get("project", {}).get("units", ""))})</text>']
        if view == 'front':
            lengths = [math.dist(w['start'], w['end']) for w in walls]
            max_x = max(lengths, default=2000)
            max_y = max([w.get('height', 2400) for w in walls] + [(p.get('elevation', 0) + (value(p, 'height') or 700)) for p in products] + [1000])
            sx, sy = 1240 / max(max_x, 1), 760 / max(max_y, 1)
            for i, wall in enumerate(walls):
                length = math.dist(wall['start'], wall['end']); base = 800 - i * 8
                body.append(f'<line x1="80" y1="{base}" x2="{80+length*sx:.1f}" y2="{base}" stroke="#80998a" stroke-width="4"/>')
                body.append(f'<text x="80" y="{base+25}" font-size="15">{html.escape(wall.get("name", wall["id"]))}</text>')
                for p in products:
                    if p.get('wall_id') != wall['id'] or not isinstance(p.get('offset'), (int,float)) or not isinstance(p.get('elevation'), (int,float)): continue
                    x, y = 80+p['offset']*sx, base-(p['elevation']+(value(p,'height') or 700))*sy
                    width, height = (value(p,'width') or 400)*sx, (value(p,'height') or 700)*sy
                    body.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{width:.1f}" height="{height:.1f}" fill="#c8dfd0" stroke="#426b54"/><text x="{x+4:.1f}" y="{y+18:.1f}">{html.escape(p["id"])}{"" if value(p,"width") and value(p,"height") else " ?"}</text>')
        elif view == 'top':
            shapes=[]; pts=[]
            for wall in walls: pts.extend([wall['start'], wall['end']])
            for p in products:
                wall=next((w for w in walls if w['id']==p.get('wall_id')),None)
                if not wall: continue
                length=math.dist(wall['start'],wall['end']) or 1; ux=(wall['end'][0]-wall['start'][0])/length; uy=(wall['end'][1]-wall['start'][1])/length; nx,ny=uy,-ux
                x=wall['start'][0]+ux*p.get('offset',0); y=wall['start'][1]+uy*p.get('offset',0); width=value(p,'width') or 400; depth=value(p,'depth') or 300
                corners=[(x,y),(x+ux*width,y+uy*width),(x+ux*width+nx*depth,y+uy*width+ny*depth),(x+nx*depth,y+ny*depth)]; pts.extend(corners); shapes.append((p,corners,not value(p,'width') or not value(p,'depth')))
            minx=min((p[0] for p in pts),default=0); maxx=max((p[0] for p in pts),default=2000); miny=min((p[1] for p in pts),default=0); maxy=max((p[1] for p in pts),default=1000); scale=min(1200/max(maxx-minx,1),700/max(maxy-miny,1))
            def xy(p): return 100+(p[0]-minx)*scale, 100+(p[1]-miny)*scale
            for wall in walls:
                a,b=xy(wall['start']),xy(wall['end']); body.append(f'<line x1="{a[0]:.1f}" y1="{a[1]:.1f}" x2="{b[0]:.1f}" y2="{b[1]:.1f}" stroke="#80998a" stroke-width="5"/>')
            for p,corners,unknown in shapes:
                points=' '.join(f'{xy(c)[0]:.1f},{xy(c)[1]:.1f}' for c in corners); body.append(f'<polygon points="{points}" fill="#c8dfd0" stroke="#426b54"/><text x="{xy(corners[0])[0]:.1f}" y="{xy(corners[0])[1]-5:.1f}">{html.escape(p["id"])}{" ?" if unknown else ""}</text>')
        else:
            # Same wall-relative box placements as the browser renderer, projected isometrically.
            boxes=[]
            for p in products:
                wall=next((w for w in walls if w['id']==p.get('wall_id')),None)
                if not wall: continue
                length=math.dist(wall['start'],wall['end']) or 1; ux=(wall['end'][0]-wall['start'][0])/length; uy=(wall['end'][1]-wall['start'][1])/length
                width,depth,height=value(p,'width') or 400,value(p,'depth') or 300,value(p,'height') or 700; angle=math.atan2(uy,ux); nx,ny=uy,-ux
                bx=wall['start'][0]+(p.get('offset',0)+width/2)*ux; by=wall['start'][1]+(p.get('offset',0)+width/2)*uy
                away=(wall.get('thickness',0)/2+depth/2); center=(bx+nx*away,(p.get('elevation',0)+height/2),by+ny*away)
                corners=[(center[0]+a*ux*width/2+b*uy*depth/2,center[1]+c*height/2,center[2]-a*uy*width/2+b*ux*depth/2) for a,b,c in [(-1,-1,-1),(1,-1,-1),(1,1,-1),(-1,1,-1),(-1,-1,1),(1,-1,1),(1,1,1),(-1,1,1)]]; boxes.append((p,corners,not all(value(p,k) for k in ('width','depth','height'))))
            # Include translucent walls and rails in the scene bounds and projection.
            wall_corners=[]
            for wall in walls:
                dx=wall['end'][0]-wall['start'][0]; dy=wall['end'][1]-wall['start'][1]; length=math.hypot(dx,dy) or 1; nx,ny=dy/length,-dx/length; thick=wall.get('thickness',100); high=wall.get('height',2400)
                for x,y in (wall['start'],wall['end']):
                    for side in (-thick/2,thick/2):
                        for z in (0,high): wall_corners.append((x+nx*side,z,y+ny*side))
            raw=[(x-z)*0.7 for _,cs,_ in boxes for x,y,z in cs]+[(x-z)*0.7 for x,y,z in wall_corners]; ys=[y-(x+z)*0.35 for _,cs,_ in boxes for x,y,z in cs]+[y-(x+z)*0.35 for x,y,z in wall_corners]; scale=min(1150/max(max(raw,default=1)-min(raw,default=0),1),700/max(max(ys,default=1)-min(ys,default=0),1)); ox=700-(max(raw,default=1)+min(raw,default=0))*scale/2; oy=470-(max(ys,default=1)+min(ys,default=0))*scale/2
            proj=lambda c:(ox+((c[0]-c[2])*0.7)*scale, oy+(c[1]-(c[0]+c[2])*0.35)*scale)
            for wall in walls:
                dx=wall['end'][0]-wall['start'][0]; dy=wall['end'][1]-wall['start'][1]; length=math.hypot(dx,dy) or 1; nx,ny=dy/length,-dx/length; thick=wall.get('thickness',100); high=wall.get('height',2400); face=[(x+nx*s,z,y+ny*s) for x,y,s,z in [(wall['start'][0],wall['start'][1],-thick/2,0),(wall['end'][0],wall['end'][1],-thick/2,0),(wall['end'][0],wall['end'][1],-thick/2,high),(wall['start'][0],wall['start'][1],-thick/2,high)]]
                points=' '.join(f'{proj(c)[0]:.1f},{proj(c)[1]:.1f}' for c in face); body.append(f'<polygon points="{points}" fill="#cedbd1" fill-opacity=".45" stroke="#a6b9ab"/>')
            for p,c,unknown in boxes:
                for face,color in [((0,1,2,3),'#aacbb6'),((0,1,5,4),'#c8dfd0'),((1,2,6,5),'#91b99f')]:
                    points=' '.join(f'{proj(c[i])[0]:.1f},{proj(c[i])[1]:.1f}' for i in face); body.append(f'<polygon points="{points}" fill="{color}" stroke="#426b54"/>')
                a=proj(c[3]); body.append(f'<text x="{a[0]:.1f}" y="{a[1]-4:.1f}">{html.escape(p["id"])}{" ?" if unknown else ""}</text>')
        body.append('<text x="28" y="875" font-size="15">Dashed/unknown placeholder geometry is approximate; ? marks missing dimensions. Verify against source drawing.</text>')
        svg=f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}"><rect width="100%" height="100%" fill="#f5f8f5"/>'+''.join(body)+'</svg>'
        (out/f'{view}.svg').write_text(svg,encoding='utf-8')
    return out


def main():
    parser=argparse.ArgumentParser(description=__doc__); parser.add_argument('model',type=Path); parser.add_argument('--out',type=Path,required=True); args=parser.parse_args()
    try: render(args.model.resolve(strict=True),args.out.resolve())
    except (OSError,ValueError,KeyError,json.JSONDecodeError) as exc: parser.exit(2,f'Render failed: {exc}\n')

if __name__=='__main__': main()
