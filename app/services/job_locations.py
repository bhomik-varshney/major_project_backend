"""Conservative structured locations, preserving original labels and explicit scope."""
from functools import lru_cache
import re
import unicodedata

COUNTRIES={'india':'India','in':'India','united states':'United States','usa':'United States','us':'United States','united kingdom':'United Kingdom','uk':'United Kingdom','germany':'Germany','canada':'Canada','singapore':'Singapore'}
CITIES={
 'bengaluru':('Bengaluru','Karnataka','India'),'bangalore':('Bengaluru','Karnataka','India'),
 'mysuru':('Mysuru','Karnataka','India'),'mysore':('Mysuru','Karnataka','India'),
 'noida':('Noida','Uttar Pradesh','India'),'lucknow':('Lucknow','Uttar Pradesh','India'),
 'delhi':('Delhi','Delhi','India'),'new delhi':('Delhi','Delhi','India'),
 'hyderabad':('Hyderabad','Telangana','India'),'pune':('Pune','Maharashtra','India'),
 'mumbai':('Mumbai','Maharashtra','India'),'chennai':('Chennai','Tamil Nadu','India'),
 'jaipur':('Jaipur','Rajasthan','India'),'ahmedabad':('Ahmedabad','Gujarat','India'),
 'kochi':('Kochi','Kerala','India'),'indore':('Indore','Madhya Pradesh','India'),
 'aligarh':('Aligarh','Uttar Pradesh','India'),
 'london':('London','England','United Kingdom'),'berlin':('Berlin','Berlin','Germany'),
 'toronto':('Toronto','Ontario','Canada'),'austin':('Austin','Texas','United States'),
 'singapore':('Singapore','Singapore','Singapore'),
}
STATES={s.casefold():(s,c) for _,s,c in CITIES.values()}


def clean(value):
    return ' '.join(re.sub(r'[,;|–—]+',' ',unicodedata.normalize('NFKC',str(value or '')).casefold()).split())


def contains(text,term):return re.search(r'(?<!\w)'+re.escape(term)+r'(?!\w)',text) is not None


@lru_cache(maxsize=32768)
def parse_location(original='',country='',work_mode=''):
    text=clean(original);remote=work_mode=='remote' or contains(text,'remote')
    city=state=region_country=None
    for key in sorted(CITIES,key=lambda s:-len(s)):
        if contains(text,key):city,state,region_country=CITIES[key];break
    if not state:
        for key,(label,nation) in STATES.items():
            if contains(text,key):state,region_country=label,nation;break
    explicit_country=COUNTRIES.get(clean(country))
    if not explicit_country:
        for key in sorted(COUNTRIES,key=lambda s:-len(s)):
            # Short country codes require a complete token; ordinary prose is not a location.
            if contains(text,key):explicit_country=COUNTRIES[key];break
    nation=explicit_country or region_country
    conflicting=bool(explicit_country and region_country and explicit_country!=region_country)
    fields=list(dict.fromkeys(v for v in (city,state,nation) if v))
    return {'original':original,'city':city,'state':state,'country':nation,'remote_status':'remote' if remote else work_mode or 'unspecified',
        'normalized_location':', '.join(fields) if fields else text,'unknown':not bool(city or state or nation or remote),'conflicting':conflicting}


def location_match(requested,job,scope='city'):
    q=parse_location(requested) if isinstance(requested,str) else requested
    value=lambda key,default='':job.get(key,default) if isinstance(job,dict) else getattr(job,key,default)
    target=parse_location(value('location'),value('country'),value('work_mode'))
    def result(accepted,relation,reason):return {'accepted':accepted,'relation':relation,'reason':reason,'requested':q,'job':target}
    if not q['original']:return result(True,'not_requested','No location constraint was requested.')
    if q['conflicting']:return result(False,'conflicting_query','The requested city/state and country conflict; correct the location.')
    if target['conflicting']:return result(False,'location_unknown','The listing has conflicting geographic metadata; review it manually.')
    same_country=bool(q['country'] and q['country']==target['country'])
    if q['country'] and target['country'] and not same_country:return result(False,'different_country','The listing country differs from the requested country.')
    query_remote=q['remote_status']=='remote'
    if query_remote:
        return result(target['remote_status']=='remote','remote_compatible' if target['remote_status']=='remote' else 'work_mode_mismatch','The listing is remote in the requested country; work authorization and time zone still require confirmation.' if target['remote_status']=='remote' else 'The request asks for remote work, but this listing is not fully remote.')
    if q['city'] and q['city']==target['city']:return result(True,'exact_city','The city matches after alias and country/state normalization.')
    if scope=='compatible' and target['remote_status']=='remote' and same_country:
        return result(True,'remote_compatible','Remote work is listed in the requested country. This is remote compatibility, not a city match; eligibility needs employer confirmation.')
    if q['city']:
        if scope=='state' and q['state'] and q['state']==target['state']:return result(True,'same_state','The listing is in the same state under the explicitly selected state-wide scope.')
        if scope=='country' and same_country:return result(True,'same_country','The listing is in the same country under the explicitly selected country-wide scope; its city differs.')
        return result(False,'different_city' if target['city'] or target['remote_status']=='remote' else 'location_unknown','The city does not match, and no applicable broader/remote scope was selected.')
    if q['state']:
        return result(q['state']==target['state'],'same_state' if q['state']==target['state'] else 'different_state','The requested state matches.' if q['state']==target['state'] else 'The listing is outside the requested state.')
    if q['country']:return result(same_country,'same_country' if same_country else 'location_unknown','The requested country matches.' if same_country else 'The listing country is unknown.')
    # Unknown locations are never widened to all jobs in a country.
    accepted=bool(q['normalized_location']) and q['normalized_location']==clean(value('location'))
    return result(accepted,'exact_label' if accepted else 'location_unknown','Unrecognized location labels match only identical normalized labels.' if accepted else 'The requested location could not be resolved to this listing.')
