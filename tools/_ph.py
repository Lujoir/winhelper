# -*- coding: utf-8 -*-
import io
p = chr(105)+chr(110)+chr(115)+chr(116)+chr(97)+chr(108)+chr(108)+chr(101)+chr(114)+chr(47)+chr(69)+chr(121)+chr(101)+chr(84)+chr(101)+chr(114)+chr(109)+chr(46)+chr(105)+chr(115)+chr(115)
s = io.open(p, encoding=chr(117)+chr(116)+chr(102)+chr(45)+chr(56)).read()
LB = chr(123); RB = chr(125); LT = chr(60); LE = chr(60)+chr(61); NE = chr(60)+chr(62); Q = chr(34)
pairs = [
 (chr(40)*2+chr(77)+chr(121)+chr(65)+chr(112)+chr(112)+chr(78)+chr(97)+chr(109)+chr(101)+chr(41)*2, LB+chr(77)+chr(121)+chr(65)+chr(112)+chr(112)+chr(78)+chr(97)+chr(109)+chr(101)+RB),
 (chr(40)*2+chr(77)+chr(121)+chr(65)+chr(112)+chr(112)+chr(69)+chr(120)+chr(101)+chr(78)+chr(97)+chr(109)+chr(101)+chr(41)*2, chr(119)+chr(105)+chr(110)+chr(104)+chr(101)+chr(108)+chr(112)+chr(101)+chr(114)+chr(46)+chr(101)+chr(120)+chr(101)),
]
for k, v in pairs:
    s = s.replace(k, v)
io.open(p, chr(119), encoding=chr(117)+chr(116)+chr(102)+chr(45)+chr(56)).write(s)
print(chr(80)+chr(49))
