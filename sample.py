#!/usr/bin/python

import cgi
import logging
import urllib2

from django.utils import simplejson

from google.appengine.api import users
from google.appengine.ext import db
from google.appengine.ext import webapp
from google.appengine.ext.webapp.util import run_wsgi_app
            
import os
from google.appengine.ext.webapp import template
import datetime

# prod
config = {'server':'https://foursquare.com',
          'api_server':'https://api.foursquare.com',
          'redirect_uri': 'https://4sqmatch.appspot.com/oauth',
          'client_id': '52AMDL0AMLMSQVYNG3FQYBXM4AF4N1THF5RBSQE5SIVQ4KOB',
          'client_secret': 'MMNBUFQBGX5SAOLXF4U002FJFKLG2V41HSRNY5W1S1C1QOBJ'}

class FS_User(db.Model):
  """Contains the user to foursquare_id + oauth token mapping."""
  user = db.UserProperty()
  fs_id = db.StringProperty()
  token = db.StringProperty()
  fs_firstName = db.StringProperty()
  fs_lastName = db.StringProperty()
  fs_photo = db.StringProperty()
  fs_gender = db.StringProperty()
  fs_homeCity = db.StringProperty()
  fs_email = db.StringProperty()
  fs_twitter = db.StringProperty()
  fs_checkins_count = db.IntegerProperty()
  last_updated = db.DateTimeProperty(auto_now_add=True)

class FS_Place(db.Model):
  """A very simple checkin object, with a denormalized userid for querying."""
  fs_id = db.StringProperty()
  fs_name = db.StringProperty()
  fs_user_id_list = db.StringListProperty()

def fetchJson(url, dobasicauth = False):
  """Does a GET to the specified URL and returns a dict representing its reply."""
  logging.info('fetching url: ' + url)
  result = urllib2.urlopen(url).read()
  logging.info('got back: ' + result)
  return simplejson.loads(result)

class OAuth(webapp.RequestHandler):
  """Handle the OAuth redirect back to the service."""
  def post(self):
    self.get()

  def get(self):
    code = self.request.get('code')
    args = dict(config)
    args['code'] = code
    url = ('%(server)s/oauth2/access_token?client_id=%(client_id)s&client_secret=%(client_secret)s&grant_type=authorization_code&redirect_uri=%(redirect_uri)s&code=%(code)s' % args)
  
    json = fetchJson(url, config['server'].find('foursquare.com') != 1)

    self_response = fetchJson('%s/v2/users/self?oauth_token=%s' % (config['api_server'], json['access_token']))

    token = FS_User(key_name=self_response['response']['user']['id'])
    token.token = json['access_token']
    token.user = users.get_current_user()
    
    token.fs_id = self_response['response']['user']['id']
    
    token.fs_firstName      = self_response['response']['user']['firstName']
    token.fs_lastName       = self_response['response']['user']['lastName']
    token.fs_photo          = self_response['response']['user']['photo'].replace('_thumbs','')
    token.fs_gender         = self_response['response']['user']['gender']
    token.fs_homeCity       = self_response['response']['user']['homeCity']
    token.fs_email          = self_response['response']['user']['contact']['email']
    token.fs_checkins_count = self_response['response']['user']['checkins']['count']
    # TODO add some sort of check before adding twitter

    token.put()

    json = fetchJson('%s/v2/users/self/checkins?limit=16&oauth_token=%s' % (config['api_server'], token.token))  
    venue_list = json['response']['checkins']['items']

    myLocalList = []
    
    for item in venue_list:
      # logging.info('the id is ' + item['venue']['id'])
      key_str = item['venue']['id']

      if FS_Place.get_by_key_name(str(key_str)) == None:
        myCheckin = FS_Place(key_name=key_str)
        myCheckin.fs_name = item['venue']['name']
        myCheckin.fs_id = item['venue']['id']
        myCheckin.fs_user_id_list.append(token.fs_id)
        myCheckin.put()
      else:
        myCheckin = FS_Place.get_by_key_name(str(key_str))
        myCheckin.fs_name = item['venue']['name']
        myCheckin.fs_id = item['venue']['id']
        if token.fs_id not in myCheckin.fs_user_id_list:
          myCheckin.fs_user_id_list.append(token.fs_id)
          if key_str not in myLocalList:
            myLocalList.append(key_str)
        if token.fs_id in myCheckin.fs_user_id_list and len(myCheckin.fs_user_id_list) > 1:
          if key_str not in myLocalList:
            myLocalList.append(key_str)
        myCheckin.put()

    # Ok now take that list of places with overlap, and get the people
    
    listOfPeopleOverlap = {}
    
    for item in myLocalList:
      # go through the places and get the list of people
      currentCheckinList = FS_Place.get_by_key_name(str(item)).fs_user_id_list
      # Add each person along with a list of the places in common
      for person in currentCheckinList:
        if person != token.fs_id:
          if person not in listOfPeopleOverlap:
            personDict = {}
            personDict['user'] = FS_User.get_by_key_name(str(person))
            personDict['places_list'] = [FS_Place.get_by_key_name(str(item))]
            personDict['places_count'] = 1
            listOfPeopleOverlap[person] = personDict
          else:
            listOfPeopleOverlap[person]['places_list'].append(FS_Place.get_by_key_name(str(item)))
            listOfPeopleOverlap[person]['places_count'] = personDict['places_count'] + 1
      
    # sort the list by popularity, with number
    # for k, v in listOfPeopleOverlap.items():
    
    doRender(self, 'results.html', {'profile_photo' : token.fs_photo,
                                    'places' : listOfPeopleOverlap} )    

class GetConfig(webapp.RequestHandler):
  """Returns the OAuth URI as JSON so the constants aren't in two places."""
  def get(self):
    uri = '%(server)s/oauth2/authenticate?client_id=%(client_id)s&response_type=code&redirect_uri=%(redirect_uri)s' % config
    self.response.out.write(simplejson.dumps({'auth_uri': uri}))

# A helper to do the rendering and to add the necessary
# variables for the _base.htm template
def doRender(handler, tname = 'index.htm', values = { }):
  temp = os.path.join(
      os.path.dirname(__file__),
      'templates/' + tname)
  if not os.path.isfile(temp):
    return False

  # Make a copy of the dictionary and add the path and session
  newval = dict(values)
  newval['path'] = handler.request.path
  outstr = template.render(temp, newval)
  handler.response.out.write(outstr)
  return True
  
application = webapp.WSGIApplication([('/oauth', OAuth), 
                                      ('/config', GetConfig)],
                                     debug=True)

def main():
  run_wsgi_app(application)

if __name__ == "__main__":
  main()
