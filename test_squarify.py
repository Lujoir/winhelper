"""squarify布局算法镜像验证（与disk.js实现逐行对应）"""
import random

def squarify_layout(data, W, H):
    rects = []
    lst = list(data)
    x = y = 0; w = W; h = H
    while lst:
        total = sum(c['value'] for c in lst)
        if total <= 0 or w <= 0 or h <= 0: break
        scale = (w * h) / total
        short = min(w, h)
        row = []; row_area = 0; worst = float('inf'); i = 0
        while i < len(lst):
            area = lst[i]['value'] * scale
            if not row:
                row.append(i); row_area = area
                t = row_area / short; l = area / t
                worst = max(t/l, l/t); i += 1; continue
            new_row_area = row_area + area
            t = new_row_area / short
            w2 = 0
            for idx in row:
                a2 = lst[idx]['value'] * scale; l2 = a2 / t
                w2 = max(w2, t/l2, l2/t)
            lc = area / t; w2 = max(w2, t/lc, lc/t)
            if w2 > worst: break
            row.append(i); row_area = new_row_area; worst = w2; i += 1
        t = row_area / short
        off = 0
        for idx in row:
            l = (lst[idx]['value'] * scale) / t
            r = {'x': x, 'y': y + off, 'w': t, 'h': l} if w >= h else {'x': x + off, 'y': y, 'w': l, 'h': t}
            r['item'] = lst[idx]; rects.append(r); off += l
        if w >= h: x += t; w -= t
        else: y += t; h -= t
        lst = lst[len(row):]
    return rects

fails = 0
for seed in range(10):
    random.seed(seed)
    data = [{'value': random.randint(1, 10000), 'name': f'd{i}'} for i in range(random.randint(5, 40))]
    rects = squarify_layout(data, 1000, 460)
    assert len(rects) == len(data), f'块数不符 seed={seed}'
    total_area = sum(r['w'] * r['h'] for r in rects)
    assert abs(total_area - 1000 * 460) < 1, f'面积不守恒 seed={seed}'
    for i in range(len(rects)):
        for j in range(i + 1, len(rects)):
            a, b = rects[i], rects[j]
            assert not (a['x'] < b['x'] + b['w'] and b['x'] < a['x'] + a['w'] and
                        a['y'] < b['y'] + b['h'] and b['y'] < a['y'] + a['h']), f'重叠 seed={seed}'
    # 大块(面积占比>3%)宽高比不超过6
    large_bad = [r for r in rects if (r['w'] * r['h']) / (1000 * 460) > 0.03 and
                 max(r['w'] / max(r['h'], 0.01), r['h'] / max(r['w'], 0.01)) > 6]
    fails += len(large_bad)

print(f'10轮随机测试: 面积守恒/无重叠 全部通过; 大块细长(占比>3%且比例>6): {fails} 个')
print('squarify布局算法验证通过')
