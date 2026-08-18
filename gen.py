import itertools
wl=set()
base=["admin","root","test","tg","tgcg","caiji","cgxt","collect","tgcj","caijixt","tgsystem","super","guanli","user","deploy","webmaster"]
suf=["123","1234","12345","123456","12345678","@123","888","666","@","123!","1234!","2024","2025","2026","168","520","007","0000","1111","6666","8888","000000","111111","666666","888888"]
passwords=set()
for b in base:
    for s in suf:
        passwords.add(b+s)
        passwords.add(b.capitalize()+s)
        passwords.add(b+"_"+s)
# numeric year variants
for y in ["2022","2023","2024","2025","2026"]:
    for pre in ["tg","admin","Admin","TG","cgxt","collect"]:
        passwords.add(pre+y); passwords.add(pre+"@"+y); passwords.add(pre+""+y+"!"); passwords.add(pre+y+"@")
    passwords.add(y); passwords.add(y+"@"+y); passwords.add("admin"+y); passwords.add("Admin"+y)
# chinese pinyin
pinyin=["tang","te","dian","huo","chao","lao","ban","qing","long","fei","hai","zhong","guo","zhg"]
for p in pinyin:
    for s in ["123","123456","@123","666","888","2024","2025","2026"]:
        passwords.add(p+s)
# add the given ssh pass and variants
passwords.update(["a161922","a161922!","A161922","161922a","a161922a","161922","@161922","tg@161922","admin@161922","161922@","a161922_"])
with open("/root/pentest/wl3.txt","w") as f:
    for p in sorted(passwords):
        f.write(p+"\n")
print("total", len(passwords))
