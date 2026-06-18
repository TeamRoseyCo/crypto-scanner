'use client'
import { useState, useEffect } from 'react'
import styles from './Counter.module.css'
import targetDateTime from '../config/dateConfig'

const Counter = () => {
  const [timeLeft, setTimeLeft] = useState({
    days: 0,
    hours: 0,
    minutes: 0
  })

  useEffect(() => {
    const timer = setInterval(() => {
      const now = new Date().getTime()
      const distance = targetDateTime - now
      
      setTimeLeft({
        days: Math.floor(distance / (1000 * 60 * 60 * 24)),
        hours: Math.floor((distance % (1000 * 60 * 60 * 24)) / (1000 * 60 * 60)),
        minutes: Math.floor((distance % (1000 * 60 * 60)) / (1000 * 60))
      })
    }, 1000)

    return () => clearInterval(timer)
  }, [])

  const FlipNumber = ({ number, label }) => (
    <div className={styles.flipContainer}>
      <div className={styles.flipNumber}>{String(number).padStart(2, '0')}</div>
      <div className={styles.label}>{label}</div>
    </div>
  )

  return (
    <div className={styles.counter}>
      <FlipNumber number={timeLeft.days} label="DAYS" />
      <FlipNumber number={timeLeft.hours} label="HOURS" />
      <FlipNumber number={timeLeft.minutes} label="MINUTES" />
    </div>
  )
}

export default Counter