# Common weak/default passwords (widely known top lists + defaults)
common = set("""
123456 123456789 12345678 1234567 password 12345 password1 1234 qwerty
111111 abc123 123123 admin 1234567890 letmein abcdef 111111qqqq 123698745
000000 654321 123456789a password123 admin123 root 888888 666666 qwerty123
1q2w3e4r qwertyuiop 12345678910 password! admin@123 12345678910 1qaz2wsx
zxcvbnm qwe123 qweasd 123456789q asdfghjkl 987654321 admin888 112233 123321
asdf1234 a123456789 12345a iloveyou welcome monkey dragon master login
hello shadow superstar whatever passw0rd p@ssw0rd P@ssw0rd pass@123
P@ssword admin2020 admin2021 admin2022 admin2023 admin2024 admin2025
Admin@123 Abc@123 a1b2c3d4 changeme default secure secret letmein123
access grant abcdefg test123 admin@2024 admin@2025 admin@2026 tg@123 tg2024
""".split())

nums=["1","2","3","12","123","1234","12345","123456","1234567","12345678","123456789",
"000000","111111","222222","333333","444444","555555","666666","777777","888888","999999",
"88888888","66666666","520520","1314520","7758521","159753","147258","369369","741852"]
basechars=["a","b","c","admin","root","test","tg","pass","qwe","abc","asd","zxc"]

# add combos up to 2 numeric suffixes
out=set(common)
for n in nums:
    out.add(n)
    for b in ["admin","Admin","root","test","tg","tgcg","caiji","collect","password","pwd","a161922"]:
        out.add(b+n); out.add(b.capitalize()+n); out.add(n+b)
# short letter+num patterns
import itertools
for n in ["1","12","123","1234","12345","123456","111","222","520","666","888"]:
    out.add("a"+n); out.add("qwe"+n); out.add("abc"+n); out.add("zxc"+n); out.add("tg"+n)
out.update(["qwertyuiop","qazwsxedc","1qaz2wsx3edc","zaq12wsx","qwe123!","@#$%^&*","!@#$%^&*","passw0rd1"])
with open("/root/pentest/wl_top.txt","w") as f:
    for p in sorted(out): f.write(p+"\n")
print("count", len(out))
